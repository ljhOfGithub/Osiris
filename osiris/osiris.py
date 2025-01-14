#!/usr/bin/env python

import shlex
import subprocess
import os
import re
import argparse
import logging
import requests
import symExec
import global_params
import z3
import z3.z3util

from source_map import SourceMap
from utils import run_command
from HTMLParser import HTMLParser

def cmd_exists(cmd):
    '''
    Runs cmd in a process and returns true if exit code is 0.
    '''
    return subprocess.call(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) == 0

def has_dependencies_installed():
    '''
    Returns true if dependencies to Z3, evm, and solc are satisfied, else returns false.
    '''
    try:
        if z3.get_version_string() != '4.6.0':
            logging.warning("You are using z3 version %s. The supported version is 4.6.0." % z3.get_version_string())
    except:
        logging.critical("Z3 is not available. Please install z3 from https://github.com/Z3Prover/z3.")
        return False

    if not cmd_exists("evm"):
        logging.critical("Please install evm from go-ethereum and make sure it is in the path.")
        return False
    else:
        cmd = "evm --version"
        out = run_command(cmd).strip()
        version = re.findall(r"evm version (\d*.\d*.\d*)", out)[0]
        if version != '1.8.3':
            logging.warning("You are using evm version %s. The supported version is 1.8.3." % version)

    if not cmd_exists("solc --version"):
        logging.critical("solc is missing. Please install the solidity compiler and make sure solc is in the path.")
        return False
    else:
        cmd = "solc --version"
        out = run_command(cmd).strip()
        version = re.findall(r"Version: (\d*.\d*.\d*)", out)[0]
        if version != '0.4.21':
            logging.warning("You are using solc version %s. The supported version is 0.4.21." % version)

    return True

def removeSwarmHash(evm):
    '''
    TODO Purpose?
    '''
    evm_without_hash = re.sub(r"a165627a7a72305820\S{64}0029$", "", evm)#替换字符串中的匹配项，a165627a7a72305820 dacdbd4746bc93ee5994b672048cef1839ae3af1c4a2382c7b0c5a2f7f37292b 0029中间一段被删除
    return evm_without_hash

def extract_bin_str(s):
    '''
    Extracts binary representation of smart contract from solc output.
    '''
    binary_regex = r"\r?\n======= (.*?) =======\r?\nBinary of the runtime part: \r?\n(.*?)\r?\n"
    contracts = re.findall(binary_regex, s)#.*?懒惰，()运算符优先级最高，返回匹配的字符串构成一个列表,?贪婪模式，
    contracts = [contract for contract in contracts if contract[1]]#除了符号表外的符号不匹配，匹配前后两个(.*?),第二个匹配成功说明编译成功
    if not contracts:
        logging.critical("Solidity compilation failed")
        print "======= error ======="
        print "Solidity compilation failed"
        exit()
    return contracts

def compileContracts(contract):
    '''
    Calls solc --bin-runtime to compile contract and returns binary representation of contract.
    调用solc——bin-runtime编译契约并返回契约的二进制表示
    '''
    cmd = "solc --bin-runtime %s" % contract#运行时编译
    out = run_command(cmd)

    libs = re.findall(r"_+(.*?)_+", out)#返回列表
    libs = set(libs)#列表转化为集合，去重
    if libs:
        return link_libraries(contract, libs)
    else:
        return extract_bin_str(out)


def link_libraries(filename, libs):
    '''
    Compiles contract in filename and links libs by calling solc --link. Returns binary representation of linked contract.
    '''
    option = ""
    for idx, lib in enumerate(libs):
        lib_address = "0x" + hex(idx+1)[2:].zfill(40)
        option += " --libraries %s:%s" % (lib, lib_address)#添加编译选项，用到额外的库的编译命令，库名和库地址
    FNULL = open(os.devnull, 'w')
    #如果需要获取 ExitCode，又不想看到输出信息。可以将 stdout 重定位到 /dev/null。
    #>>> null = open(os.devnull, "w")
    #>>> call(split("ls -l"), stdout = null, stderr = null)
    #0
    cmd = "solc --bin-runtime %s" % filename
    p1 = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE, stderr=FNULL)#省略出错信息
    cmd = "solc --link%s" %option
    p2 = subprocess.Popen(shlex.split(cmd), stdin=p1.stdout, stdout=subprocess.PIPE, stderr=FNULL)
    p1.stdout.close()
    out = p2.communicate()[0]#得到p2执行的输出结果，[0]表示stdout的值
    return extract_bin_str(out)

def analyze(processed_evm_file, disasm_file, source_map = None):
    #processed_evm_file：'datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory.evm'
    #<source_map.SourceMap instance at 0x7eff52a728c0>
    '''Runs the symbolic execution.
    disasm_file：'datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory.evm.disasm'
        Parameters
    ----------
    processed_evm_file : File descriptor of EVM bytecode file on which "removeSwarmHash" has been removed  TODO: Why not remove this argument and process disasm_file as necessary within analyze()? This way, the function makes implicit assumptions about the relation between those two arguments.
    已删除“removeSwarmHash”的EVM字节码文件的文件描述符
    disasm_file: File descriptor of the original EVM asm file
    原始EVM asm文件的文件描述符
    source_map: SourceMap of compiled contracts
    '''
    disasm_out = ""

    # Check if processed_evm_file can be disassembled检查processsed_evm_file是否可以被反汇编
    # TODO: Why this check? The result is not used anyway and it is not said that processed_evm_file is related to disasm_file.
    try:
        disasm_p = subprocess.Popen(
            ["evm", "disasm", processed_evm_file], stdout=subprocess.PIPE)#disasm_p：<subprocess.Popen object at 0x7eff5225a210>
        disasm_out = disasm_p.communicate()[0]#disasm_out是stdout,[1]是stderr，将evm文件进行反编译
    except:
        logging.critical("Disassembly failed.")
        exit()

    with open(disasm_file, 'w') as of:
        of.write(disasm_out)

    # Run symExec
    if source_map is not None:
        symExec.main(disasm_file, args.source, source_map)#需要等待符号执行，关键断点
    else:
        symExec.main(disasm_file, args.source)

def remove_temporary_file(path):
    '''Does what it says (no matter if the file was temporary).
    '''
    if os.path.isfile(path):
        try:
            os.unlink(path)#用于删除文件,如果文件是一个目录则返回一个错误。
        except:
            pass

def main():
    global args

    print("")
    print("  .oooooo.             o8o            o8o          ")
    print(" d8P'  `Y8b            `\"'            `\"'          ")
    print("888      888  .oooo.o oooo  oooo d8b oooo   .oooo.o")
    print("888      888 d88(  \"8 `888  `888\"\"8P `888  d88(  \"8")
    print("888      888 `\"Y88b.   888   888      888  `\"Y88b. ")
    print("`88b    d88' o.  )88b  888   888      888  o.  )88b")
    print(" `Y8bood8P'  8\"\"888P' o888o d888b    o888o 8\"\"888P'")
    print("")

    parser = argparse.ArgumentParser()# argparse.py:1272，python2.7自带库
    group = parser.add_mutually_exclusive_group(required=True)# 自带库
    group.add_argument("-s", "--source", type=str,
                       help="local source file name. Solidity by default. Use -b to process evm instead. Use stdin to read from stdin.")
    group.add_argument("-ru", "--remoteURL", type=str,
                       help="Get contract from remote URL. Solidity by default. Use -b to process evm instead.", dest="remote_URL")

    parser.add_argument("--version", action="version", version="Osiris version 0.0.1 - 'Memphis' (Oyente version 0.2.7 - Commonwealth)")
    parser.add_argument(
        "-b", "--bytecode", help="read bytecode in source instead of solidity file.", action="store_true")

    parser.add_argument(
        "-j", "--json", help="Redirect results to a json file.", action="store_true")
    parser.add_argument(
        "-e", "--evm", help="Do not remove the .evm file.", action="store_true")
    parser.add_argument(
        "-p", "--paths", help="Print path condition information.", action="store_true")
    parser.add_argument(
        "--error", help="Enable exceptions and print output. Monsters here.", action="store_true")
    parser.add_argument("-t", "--timeout", type=int, help="Timeout for Z3 in ms (default "+str(global_params.TIMEOUT)+" ms).")
    parser.add_argument(
        "-v", "--verbose", help="Verbose output, print everything.", action="store_true")#详细输出，打印一切
    parser.add_argument(
        "-r", "--report", help="Create .report file.", action="store_true")
    parser.add_argument("-gb", "--globalblockchain",
                        help="Integrate with the global ethereum blockchain", action="store_true")
    parser.add_argument("-dl", "--depthlimit", help="Limit DFS depth (default "+str(global_params.DEPTH_LIMIT)+").",
                        action="store", dest="depth_limit", type=int)
    parser.add_argument("-gl", "--gaslimit", help="Limit Gas (default "+str(global_params.GAS_LIMIT)+").",
                        action="store", dest="gas_limit", type=int)
    parser.add_argument(
        "-st", "--state", help="Get input state from state.json", action="store_true")
    parser.add_argument("-ll", "--looplimit", help="Limit number of loops (default "+str(global_params.LOOP_LIMIT)+").",
                        action="store", dest="loop_limit", type=int)
    parser.add_argument(
        "-w", "--web", help="Run Osiris for web service", action="store_true")#
    parser.add_argument("-glt", "--global-timeout", help="Timeout for symbolic execution in sec (default "+str(global_params.GLOBAL_TIMEOUT)+" sec).", action="store", dest="global_timeout", type=int)
    parser.add_argument(
        "-a", "--assertion", help="Check assertion failures.", action="store_true")#检查断言错误
    parser.add_argument(
            "--debug", help="Display debug information", action="store_true")
    parser.add_argument(
        "--generate-test-cases", help="Generate test cases each branch of symbolic execution tree", action="store_true")
    parser.add_argument(
        "-c", "--cfg", help="Create control flow graph and store as .dot file.", action="store_true")#创建控制流图
    parser.add_argument(
        "-m", "--model", help="Output models generated by the solver.", action="store_true")

    args = parser.parse_args()#使用添加的参数和值生成namespace对象，存放属性，所有合约的分析都基于一开始传入的所有参数

    # Set global arguments for symbolic execution 根据命令行传入的参数，设置符号执行的全局变量
    if args.timeout:
        global_params.TIMEOUT = args.timeout

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)
    global_params.PRINT_PATHS = 1 if args.paths else 0
    global_params.REPORT_MODE = 1 if args.report else 0
    global_params.IGNORE_EXCEPTIONS = 1 if args.error else 0
    global_params.USE_GLOBAL_BLOCKCHAIN = 1 if args.globalblockchain else 0
    global_params.INPUT_STATE = 1 if args.state else 0
    global_params.WEB = 1 if args.web else 0
    global_params.STORE_RESULT = 1 if args.json else 0
    global_params.CHECK_ASSERTIONS = 1 if args.assertion else 0
    global_params.DEBUG_MODE = 1 if args.debug else 0
    global_params.GENERATE_TEST_CASES = 1 if args.generate_test_cases else 0
    global_params.CFG = 1 if args.cfg else 0
    global_params.MODEL_INPUT = 1 if args.model else 0

    if args.depth_limit:
        global_params.DEPTH_LIMIT = args.depth_limit
    if args.gas_limit:
        global_params.GAS_LIMIT = args.gas_limit
    if args.loop_limit:
        global_params.LOOP_LIMIT = args.loop_limit
    if global_params.WEB:
        if args.global_timeout and args.global_timeout < global_params.GLOBAL_TIMEOUT:
            global_params.GLOBAL_TIMEOUT = args.global_timeout
    else:
        if args.global_timeout:
            global_params.GLOBAL_TIMEOUT = args.global_timeout

    # Check that our system has everything we need (evm, Z3) 检查依赖
    if not has_dependencies_installed():
        return

    # Retrieve contract from remote URL, if necessary 
    if args.remote_URL:
        r = requests.get(args.remote_URL)
        code = r.text
        filename = "remote_contract.evm" if args.bytecode else "remote_contract.sol"
        if "etherscan.io" in args.remote_URL and not args.bytecode:
            try:
                filename = re.compile('<td>Contract<span class="hidden-su-xs"> Name</span>:</td><td>(.+?)</td>').findall(code.replace('\n','').replace('\t',''))[0].replace(' ', '')
                filename += ".sol"
            except:
                pass
            code = re.compile("<pre class='js-sourcecopyarea' id='editor' style='.+?'>([\s\S]+?)</pre>", re.MULTILINE).findall(code)[0]
            code = HTMLParser().unescape(code)
        args.source = filename
        with open(filename, 'w') as f:
            f.write(code)
    # 如果有字节码参数，先反汇编，然后根据evm汇编码操作
    # If we are given bytecode, disassemble first, as we need to operate on EVM ASM.
    if args.bytecode:
        processed_evm_file = args.source + '.evm'
        disasm_file = args.source + '.evm.disasm'
        with open(args.source) as f:
            evm = f.read()

        with open(processed_evm_file, 'w') as f:
            f.write(removeSwarmHash(evm))

        analyze(processed_evm_file, disasm_file)#main->analyze->symexec.main()->symexec.py closing_message();Mallory->Mallory2->SimpleDAO

        remove_temporary_file(disasm_file)#删除evm.disasm文件
        remove_temporary_file(processed_evm_file)#删除evm文件
        remove_temporary_file(disasm_file + '.log')#删除log文件

        if global_params.UNIT_TEST == 2 or global_params.UNIT_TEST == 3:
            exit_code = os.WEXITSTATUS(cmd)
            if exit_code != 0:
                exit(exit_code)
    else:
        # Compile contracts using solc
        contracts = compileContracts(args.source)#编译solidity文件，contracts是list，元素是tuple，tuple的第一个是'solidity文件名：合约名'的字符串

        # Analyze each contract
        for cname, bin_str in contracts:#二元tuple列表的遍历，按照编译好的合约顺序进行分析，顺序由编译器决定
            print("")
            logging.info("Contract %s:", cname)
            processed_evm_file = cname + '.evm'#cname：'datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory'
            disasm_file = cname + '.evm.disasm'#cname：'datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory'

            with open(processed_evm_file, 'w') as of:
                of.write(removeSwarmHash(bin_str))#去除swarm hash后存储

            analyze(processed_evm_file, disasm_file, SourceMap(cname, args.source))#cname：'datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory'，analyze()->source_map.py->ast_helper.py
            #需要等待执行
            remove_temporary_file(processed_evm_file)
            remove_temporary_file(disasm_file)
            remove_temporary_file(disasm_file + '.log')

            if args.evm:
                with open(processed_evm_file, 'w') as of:#如果需要，可以不去除swarm hash
                    of.write(bin_str)

        if global_params.STORE_RESULT:
            if ':' in cname:
                result_file = os.path.join(global_params.RESULTS_DIR, cname.split(':')[0].replace('.sol', '.json').split('/')[-1])
                with open(result_file, 'a') as of:
                    of.write("}")

if __name__ == '__main__':
    main()



#INFO:symExec:     Concurrency bug:       True
# Flow 1:
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:61:16
# owner.send(this.balance)
# ^
# Flow 2:
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:55:5
# dao.donate.value(1)(this)
#INFO:symExec:     Time dependency bug:   False
# INFO:symExec:     Reentrancy bug:        True
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:56:5
# dao.withdraw(1)
# ^
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:55:5
# dao.donate.value(1)(this)
# ^
# INFO:symExec:    --- 3.97241997719 seconds ---
# INFO:symExec:   ====== Analysis Completed ======
# --Return--





#INFO:symExec:   ============ Results ===========
# INFO:symExec:     EVM code coverage:     99.8%
# INFO:symExec:     Arithmetic bugs:       False
# INFO:symExec:     └> Overflow bugs:      False
# INFO:symExec:     └> Underflow bugs:     False
# INFO:symExec:     └> Division bugs:      False
# INFO:symExec:     └> Modulo bugs:        False
# INFO:symExec:     └> Truncation bugs:    False
# INFO:symExec:     └> Signedness bugs:    False
# INFO:symExec:     Callstack bug:         True
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:61:16
# owner.send(this.balance)
# ^
# INFO:symExec:     Concurrency bug:       True
# Flow 1:
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:61:16
# owner.send(this.balance)
# ^
# Flow 2:
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:55:5
# dao.donate.value(1)(this)
# ^
# INFO:symExec:     Time dependency bug:   False
# INFO:symExec:     Reentrancy bug:        True
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:56:5
# dao.withdraw(1)
# ^
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:55:5
# dao.donate.value(1)(this)
# ^
# INFO:symExec:    --- 3.7286169529 seconds ---
# INFO:symExec:   ====== Analysis Completed ======
# --Return--




# INFO:symExec:Running, please wait...
# INFO:symExec:   ============ Results ===========
# INFO:symExec:     EVM code coverage:     99.7%
# INFO:symExec:     Arithmetic bugs:       True
# INFO:symExec:     └> Overflow bugs:      True
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:SimpleDAO:7:5
# credit[to] += msg.value
# ^
# INFO:symExec:     └> Underflow bugs:     True
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:SimpleDAO:13:7
# credit[msg.sender]-=amount
# ^
# INFO:symExec:     └> Division bugs:      False
# INFO:symExec:     └> Modulo bugs:        False
# INFO:symExec:     └> Truncation bugs:    False
# INFO:symExec:     └> Signedness bugs:    False
# INFO:symExec:     Callstack bug:         True
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:SimpleDAO:12:18
# msg.sender.call.value(amount)()
# ^
# INFO:symExec:     Concurrency bug:       False
# INFO:symExec:     Time dependency bug:   False
# INFO:symExec:     Reentrancy bug:        True
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:SimpleDAO:12:18
# msg.sender.call.value(amount)()
# ^
# INFO:symExec:    --- 20.1363739967 seconds ---
# INFO:symExec:   ====== Analysis Completed ======
# The program finished and will be restarted





# INFO:symExec:Running, please wait...
# INFO:symExec:   ============ Results ===========
# INFO:symExec:     EVM code coverage:     99.8%
# INFO:symExec:     Arithmetic bugs:       False
# INFO:symExec:     └> Overflow bugs:      False
# INFO:symExec:     └> Underflow bugs:     False
# INFO:symExec:     └> Division bugs:      False
# INFO:symExec:     └> Modulo bugs:        False
# INFO:symExec:     └> Truncation bugs:    False
# INFO:symExec:     └> Signedness bugs:    False
# INFO:symExec:     Callstack bug:         True
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:61:16
# owner.send(this.balance)
# ^
# INFO:symExec:     Concurrency bug:       True
# Flow 1:
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:61:16
# owner.send(this.balance)
# ^
# Flow 2:
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:55:5
# dao.donate.value(1)(this)
# ^
# INFO:symExec:     Time dependency bug:   False
# INFO:symExec:     Reentrancy bug:        True
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:56:5
# dao.withdraw(1)
# ^
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:55:5
# dao.donate.value(1)(this)
# ^
# INFO:symExec:    --- 4.04498291016 seconds ---
# INFO:symExec:   ====== Analysis Completed ======

# INFO:root:Contract datasets/SimpleDAO/SimpleDAO_0.4.19.sol:SimpleDAO:






# INFO:root:Contract datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory:
# INFO:symExec:Running, please wait...
# INFO:symExec:   ============ Results ===========
# INFO:symExec:     EVM code coverage:     99.7%
# INFO:symExec:     Arithmetic bugs:       False
# INFO:symExec:     └> Overflow bugs:      False
# INFO:symExec:     └> Underflow bugs:     False
# INFO:symExec:     └> Division bugs:      False
# INFO:symExec:     └> Modulo bugs:        False
# INFO:symExec:     └> Truncation bugs:    False
# INFO:symExec:     └> Signedness bugs:    False
# INFO:symExec:     Callstack bug:         False
# INFO:symExec:     Concurrency bug:       False
# INFO:symExec:     Time dependency bug:   False
# INFO:symExec:     Reentrancy bug:        False
# INFO:symExec:    --- 0.263648986816 seconds ---
# INFO:symExec:   ====== Analysis Completed ======

# INFO:root:Contract datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:
# INFO:symExec:Running, please wait...
# INFO:symExec:   ============ Results ===========
# INFO:symExec:     EVM code coverage:     99.8%
# INFO:symExec:     Arithmetic bugs:       False
# INFO:symExec:     └> Overflow bugs:      False
# INFO:symExec:     └> Underflow bugs:     False
# INFO:symExec:     └> Division bugs:      False
# INFO:symExec:     └> Modulo bugs:        False
# INFO:symExec:     └> Truncation bugs:    False
# INFO:symExec:     └> Signedness bugs:    False
# INFO:symExec:     Callstack bug:         True
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:61:16
# owner.send(this.balance)
# ^
# INFO:symExec:     Concurrency bug:       True
# Flow 1:
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:61:16
# owner.send(this.balance)
# ^
# Flow 2:
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:55:5
# dao.donate.value(1)(this)
# ^
# INFO:symExec:     Time dependency bug:   False
# INFO:symExec:     Reentrancy bug:        True
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:56:5
# dao.withdraw(1)
# ^
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2:55:5
# dao.donate.value(1)(this)
# ^
# INFO:symExec:    --- 0.513692855835 seconds ---
# INFO:symExec:   ====== Analysis Completed ======

# INFO:root:Contract datasets/SimpleDAO/SimpleDAO_0.4.19.sol:SimpleDAO:
# INFO:symExec:Running, please wait...
# INFO:symExec:   ============ Results ===========
# INFO:symExec:     EVM code coverage:     99.7%
# INFO:symExec:     Arithmetic bugs:       True
# INFO:symExec:     └> Overflow bugs:      True
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:SimpleDAO:7:5
# credit[to] += msg.value
# ^
# INFO:symExec:     └> Underflow bugs:     True
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:SimpleDAO:13:7
# credit[msg.sender]-=amount
# ^
# INFO:symExec:     └> Division bugs:      False
# INFO:symExec:     └> Modulo bugs:        False
# INFO:symExec:     └> Truncation bugs:    False
# INFO:symExec:     └> Signedness bugs:    False
# INFO:symExec:     Callstack bug:         True
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:SimpleDAO:12:18
# msg.sender.call.value(amount)()
# ^
# INFO:symExec:     Concurrency bug:       False
# INFO:symExec:     Time dependency bug:   False
# INFO:symExec:     Reentrancy bug:        True
# datasets/SimpleDAO/SimpleDAO_0.4.19.sol:SimpleDAO:12:18
# msg.sender.call.value(amount)()
# ^
# INFO:symExec:    --- 0.890538930893 seconds ---
# INFO:symExec:   ====== Analysis Completed ======
# The program finished and will be restarted