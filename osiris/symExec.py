import tokenize
import sha3
from tokenize import NUMBER, NAME, NEWLINE
import re
import math
import sys
import pickle
import json
import traceback
import signal
import time
import logging
import os.path
import z3
import binascii

from collections import namedtuple
from vargenerator import *
from ethereum_data import *
from basicblock import BasicBlock
from analysis import *
from test_evm.global_test_params import (TIME_OUT, UNKOWN_INSTRUCTION,
                                         EXCEPTION, PICKLE_PATH)
from validator import Validator
import global_params

from intFlow import *
from taintFlow import *

import web3
from web3 import Web3, IPCProvider

log = logging.getLogger(__name__)#返回具有指定 name 的日志记录器，或者当 name 为 None 时返回层级结构中的根日志记录器。

UNSIGNED_BOUND_NUMBER = 2**256 - 1#无符号数的最大数
CONSTANT_ONES_159 = BitVecVal((1 << 160) - 1, 256)

Assertion = namedtuple('Assertion', ['pc', 'model'])

class Parameter:
    def __init__(self, **kwargs):
        attr_defaults = {
            "instr": "",
            "block": 0,
            "depth": 0,
            "pre_block": 0,
            "func_call": -1,
            "stack": [],
            "calls": [],
            "memory": [],
            "models": [],
            "visited": [],
            "mem": {},
            "analysis": {},
            "sha3_list": {},
            "global_state": {},
            "path_conditions_and_vars": {}
        }
        for (attr, default) in attr_defaults.iteritems():#设置属性默认值，并且使用传入的参数设置对应初始值，如果没有对应的参数，则使用attr_defaults中设置的默认值，iteritems遍历字典的所有值
            setattr(self, attr, kwargs.get(attr, default))#kwargs：字典类型，就是初始的值{'global_state': {'origin': tx.origin, 'gas_price': tx.gasprice, 'currentTimestamp': IH_s, 'miu_i': 0, 'currentCoinbase': IH_c, 'value': Iv, 'sender_address': Is, 'pc': 0, 'currentDifficulty': IH_d, 'Ia': {}, 'currentGasLimit': IH_l, 'receiver_address': Ia, 'balance': {'Ia': init_Ia + Iv, 'Is': init_Is - Iv}, 'currentNumber': IH_i}, 'path_conditions_and_vars': {'path_condition': [Iv >= 0, init_Is >= Iv, init_Ia >= 0], 'IH_s': IH_s, 'tx.origin': tx.origin, 'Is': Is, 'Iv': Iv, 'IH_d': IH_d, 'tx.gasprice': tx.gasprice, 'IH_c': IH_c, 'Ia': Ia, 'IH_l': IH_l, 'IH_i': IH_i}, 'analysis': {'sstore': {}, 'money_flow': [('Is', 'Ia', 'Iv')], 'sload': [], 'reentrancy_bug': [], 'money_concurrency_bug': [], 'gas_mem': 0, 'gas': 0, 'time_dependency_bug': {}}}

    def copy(self):
        _kwargs = custom_deepcopy(self.__dict__)
        return Parameter(**_kwargs)

def initGlobalVars():
    global solver
    # Z3 solver
    solver = Solver()
    solver.set("timeout", global_params.TIMEOUT)#global_params.TIMEOUT=100

    global visited_pcs
    visited_pcs = set()#初始化为set([])

    global results
    results = {
        "evm_code_coverage": "", "callstack": False, "money_concurrency": False,
        "time_dependency": False, "reentrancy": False, "assertion_failure": False,
        "overflow": False, "underflow": False, "truncation": False, "signedness": False,
        "division": False, "modulo": False, "execution_time": "", "dead_code": [],
        "execution_paths": "", "timeout": False
    }

    global g_timeout
    g_timeout = False

    global arithmetic_errors
    arithmetic_errors = []

    global type_information
    type_information = {}

    global width_conversions
    width_conversions = []

    global arithmetic_models
    arithmetic_models = {}

    # capturing the last statement of each basic block
    # 捕获每个基本块的最后一个语句 end_instruction_dictionary
    global end_ins_dict#声明全局变量
    end_ins_dict = {}

    # capturing all the instructions, keys are corresponding addresses
    # 捕获所有的指令，键是对应的地址
    global instructions
    instructions = {}

    # capturing the "jump type" of each basic block
    global jump_type
    jump_type = {}

    global vertices
    vertices = {}

    global edges
    edges = {}

    global visited_edges
    visited_edges = {}

    global money_flow_all_paths
    money_flow_all_paths = []

    global reentrancy_all_paths
    reentrancy_all_paths = []#可重入路径

    global data_flow_all_paths
    data_flow_all_paths = [[], []] # store all storage addresses存储所有内存地址

    # store the path condition corresponding to each path in money_flow_all_paths
    #在money_flow_all_paths中存储每个路径对应的路径条件
    global path_conditions
    path_conditions = []

    global global_problematic_pcs#记录所有出错的pc位置，列表外嵌套字典
    global_problematic_pcs = {"money_concurrency_bug": [], "reentrancy_bug": [], "time_dependency_bug": [], "assertion_failure": []}

    # store global variables, e.g. storage, balance of all paths
    #存储全局变量，例如存储，所有路径的平衡
    global all_gs
    all_gs = []

    global total_no_of_paths
    total_no_of_paths = 0

    global no_of_test_cases
    no_of_test_cases = 0

    # to generate names for symbolic variables
    # 来为符号变量生成符号变量的名称
    global gen
    gen = Generator()

    global data_source
    if global_params.USE_GLOBAL_BLOCKCHAIN:#没传入
        data_source = EthereumData()

    global log_file #'datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory.evm.disasm'
    log_file = open(c_name + '.log', "w")

    global rfile
    if global_params.REPORT_MODE:
        rfile = open(c_name + '.report', 'w')

def check_unit_test_file():#检查单元测试文件
    if global_params.UNIT_TEST == 1:
        try:
            open('unit_test.json', 'r')
        except:
            log.critical("Could not open result file for unit test")
            exit()

def isTesting():
    return global_params.UNIT_TEST != 0

# A simple function to compare the end stack with the expected stack
# configurations specified in a test file
#一个简单的函数，用来比较结束堆栈和预期堆栈
#测试文件中指定的配置
def compare_stack_unit_test(stack):
    try:
        size = int(result_file.readline())
        content = result_file.readline().strip('\n')
        if size == len(stack) and str(stack) == content:
            log.debug("PASSED UNIT-TEST")
        else:
            log.warning("FAILED UNIT-TEST")
            log.warning("Expected size %d, Resulted size %d", size, len(stack))
            log.warning("Expected content %s \nResulted content %s", content, str(stack))
    except Exception as e:
        log.warning("FAILED UNIT-TEST")
        log.warning(e.message)

def compare_storage_and_gas_unit_test(global_state, analysis):
    unit_test = pickle.load(open(PICKLE_PATH, 'rb'))
    test_status = unit_test.compare_with_symExec_result(global_state, analysis)
    exit(test_status)

def change_format():#格式化反汇编文件
    with open(c_name) as disasm_file:#'datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory.evm.disasm'
        file_contents = disasm_file.readlines()#
        i = 0#file_contents[0]是尾部带\n的字节码
        firstLine = file_contents[0].strip('\n')#移除第一行字符串头尾指定的字符（默认为空格或换行符）或字符序列
        for line in file_contents:
            line = line.replace('SELFDESTRUCT', 'SUICIDE')#
            line = line.replace('Missing opcode 0xfd', 'REVERT')
            line = line.replace('Missing opcode 0xfe', 'ASSERTFAIL')
            line = line.replace('Missing opcode', 'INVALID')
            line = line.replace(':', '')#替换后如'000000 PUSH1 0x60\n'
            lineParts = line.split(' ')#每行拆分成序号和指令，如['000000', 'PUSH1', '0x60\n']
            try: # removing initial zeroes
                lineParts[0] = str(int(lineParts[0]))#如果不是字节码而是序号则存储为不带前面的0的字符串，第二行是0

            except:
                lineParts[0] = lineParts[0]#如果是十六进制（字节码）则触发异常直接存储
            lineParts[-1] = lineParts[-1].strip('\n')#去除evm.disasm的每行的换行符，存储每行最后一个参数，第二行是0x60
            try: # adding arrow if last is a number如果最后是一个数字，添加箭头
                lastInt = lineParts[-1]#每行最后一个字符串，第二行是0x60
                if(int(lastInt, 16) or int(lastInt, 16) == 0) and len(lineParts) > 2:#如果转换为16进制后，lineParts长度>2
                    lineParts[-1] = "=>"#int(lastInt, 16)是96，len(lineParts)是3，这段的作用是往中间添加一个箭头
                    lineParts.append(lastInt)#添加后是：['0', 'PUSH1', '=>', '0x60']
            except Exception:
                pass
            file_contents[i] = ' '.join(lineParts)#file_contents[0]是字节码，将数组转换为字符串
            i = i + 1
        file_contents[0] = firstLine#首个元素设置为字节码，这步是多余的
        file_contents[-1] += '\n'#'743 STOP'加换行符

    with open(c_name, 'w') as disasm_file:#重写回反汇编文件
        disasm_file.write("\n".join(file_contents))

def build_cfg_and_analyze():
    global source_map

    change_format()#格式化evm.disasm文件，首行是字节码，剩余是解析的指令，有序号，指令，参数等
    with open(c_name, 'r') as disasm_file:
        disasm_file.readline()  # Remove first line；移除'datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory.evm.disasm'文件首行字节码后，将evm.disasm文件剩余部分构造成tokens对象
        tokens = tokenize.generate_tokens(disasm_file.readline)#以编程方式对文件进行标记的例子，用 generate_tokens() 读取 unicode 字符串而不是字节；tokens是一个具名数组的迭代器 
        #生成器产生 5 个具有这些成员的元组：令牌类型；令牌字符串；指定令牌在源中开始的行和列的 2 元组 (srow, scol) ；指定令牌在源中结束的行和列的 2 元组 (erow, ecol) ；以及发现令牌的行。所传递的行（最后一个元组项）是 实际的 行。 5 个元组以 named tuple 的形式返回，字段名是： type string start end line 。
        collect_vertices(tokens)#根据指令构造节点，收集一个块的节点后，构造基本块
        construct_bb()#构造基本块
        construct_static_edges()#构造基本块的静态边
        full_sym_exec()  # jump targets are constructed on the fly跳跃目标是在飞行中构造的
        if global_params.CFG:
            print_cfg()

def print_cfg():
    f = open(c_name.replace('.disasm', '').replace(':', '-')+'.dot', 'w')
    f.write('digraph osiris_cfg {\n')
    f.write('rankdir = TB;\n')
    f.write('size = "240"\n')
    f.write('graph[fontname = Courier, fontsize = 14.0, labeljust = l, nojustify = true];node[shape = record];\n')
    address_width = 10
    if len(hex(instructions.keys()[-1])) > address_width:
        address_width = len(hex(instructions.keys()[-1]))
    for block in vertices.values():
        #block.display()
        address = block.get_start_address()
        label = '"'+hex(block.get_start_address())+'"[label="'
        error = False
        for instruction in block.get_instructions():
            error_list = [arithmetic_error for arithmetic_error in arithmetic_errors if arithmetic_error["pc"] == address and arithmetic_error["validated"]]
            if len(error_list) > 0:
                error = True
                label += "{0:#0{1}x}".format(address, address_width)+" "+instruction+" **[Error: "+error_list[0]["type"]+"]**"+"\l"
            else:
                label += "{0:#0{1}x}".format(address, address_width)+" "+instruction+"\l"
            address += 1 + (len(instruction.split(' ')[1].replace("0x", "")) / 2)
        if error:
            f.write(label+'",style=filled,color=red];\n')
        else:
            f.write(label+'"];\n')
        if block.get_block_type() == "conditional":
            if len(edges[block.get_start_address()]) > 1:
                f.write('"'+hex(block.get_start_address())+'" -> "'+hex(edges[block.get_start_address()][1])+'" [color="green" label=" '+str(block.get_branch_expression())+'"];\n')
                f.write('"'+hex(block.get_start_address())+'" -> "'+hex(edges[block.get_start_address()][0])+'" [color="red" label=" '+str(Not(block.get_branch_expression()))+'"];\n')
            else:
                f.write('"'+hex(block.get_start_address())+'" -> "UNKNOWN_TARGET" [color="black" label=" UNKNOWN_BRANCH_EXPR"];\n')
                f.write('"'+hex(block.get_start_address())+'" -> "'+hex(edges[block.get_start_address()][0])+'" [color="black"];\n')
        elif block.get_block_type() == "unconditional" or block.get_block_type() == "falls_to":
            if len(edges[block.get_start_address()]) > 0:
                f.write('"'+hex(block.get_start_address())+'" -> "'+hex(edges[block.get_start_address()][0])+'" [color="black"];\n')
            else:
                f.write('"'+hex(block.get_start_address())+'" -> "UNKNOWN_TARGET" [color="black"];\n')
    f.write('}\n')
    f.close()
    log.debug(str(edges))

def mapping_push_instruction(current_line_content, current_ins_address, idx, positions, length):#映射push汇编指令
    global source_map

    while (idx < length):
        if not positions[idx]:#如果index处为0，则初始化为1
            return idx + 1
        name = positions[idx]['name']
        if name.startswith("tag"):
            idx += 1
        else:
            if name.startswith("PUSH"):#push指令
                if name == "PUSH":
                    value = positions[idx]['value']
                    instr_value = current_line_content.split(" ")[1]
                    if int(value, 16) == int(instr_value, 16):#指令位置字典，指令地址：
                        source_map.instr_positions[current_ins_address] = source_map.positions[idx]#source_map.instr_positions[current_ins_address]有：begin，end，name（指令名），value（需要push的值）
                        idx += 1#current_ins_address是push指令在instr_positions字典中的下标
                        break;
                    else:
                        raise Exception("Source map error")
                else:
                    source_map.instr_positions[current_ins_address] = source_map.positions[idx]
                    idx += 1
                    break;
            else:
                raise Exception("Source map error")
    return idx#最新的source_map.positions的下标

def mapping_non_push_instruction(current_line_content, current_ins_address, idx, positions, length):#映射非push指令到index
    global source_map

    while (idx < length):
        if not positions[idx]:
            return idx + 1
        name = positions[idx]['name']
        if name.startswith("tag"):
            idx += 1
        else:
            instr_name = current_line_content.split(" ")[0]
            if name == instr_name or name == "INVALID" and instr_name == "ASSERTFAIL" or name == "KECCAK256" and instr_name == "SHA3" or name == "SELFDESTRUCT" and instr_name == "SUICIDE":
                source_map.instr_positions[current_ins_address] = source_map.positions[idx]
                idx += 1
                break;
            else:
                raise Exception("Source map error")
    return idx#最新的source_map.positions的下标

# 1. Parse the disassembled file
# 2. Then identify each basic block (i.e. one-in, one-out)
# 3. Store them in vertices
def collect_vertices(tokens):
    global source_map#声明全局变量
    if source_map:#初始化
        idx = 0
        positions = source_map.positions
        length = len(positions)#327
    global end_ins_dict
    global instructions
    global jump_type

    current_ins_address = 0
    last_ins_address = 0
    is_new_line = True
    current_block = 0
    current_line_content = ""
    wait_for_push = False
    is_new_block = False

    for tok_type, tok_string, (srow, scol), _, line_number in tokens:#处理tokenInfo对象，也是指令对象，包含指令的类型，具体的指令，指令的行号列号，舍弃最后发现的指令位置，如果没有用step进去则会跳过调试
        if wait_for_push is True:#push后面的参数，push指令，第一行不是push
            push_val = ""#定义的指令类型：1是汇编指令NAME，2是立即数NUMBER，4是换行符NEW LINE，5是等于号=
            for ptok_type, ptok_string, _, _, _ in tokens:#
                if ptok_type == NEWLINE:#含换行符的行
                    is_new_line = True
                    current_line_content += push_val + ' '#初始为空字符串
                    instructions[current_ins_address] = current_line_content#0索引处为空字符串
                    idx = mapping_push_instruction(current_line_content, current_ins_address, idx, positions, length) if source_map else None
                    log.debug(current_line_content)
                    current_line_content = ""
                    wait_for_push = False
                    break
                try:#不含换行符的行
                    int(ptok_string, 16)
                    push_val += ptok_string#ptok_string具体值例子：PUSH1,push_val是具体的PUSH指令列表
                except ValueError:
                    pass
            continue
        elif is_new_line is True and tok_type == NUMBER:  # looking for a line number,NUMBER是tokenize库中定义的python的数字转换为字符串后的形式，这里一般是整数的字符串形式
            last_ins_address = current_ins_address
            try:
                current_ins_address = int(tok_string)#tok_string此时是指令的下标（猜测）
            except ValueError:
                log.critical("ERROR when parsing row %d col %d", srow, scol)
                quit()
            is_new_line = False
            if is_new_block:
                current_block = current_ins_address
                is_new_block = False
            continue
        elif tok_type == NEWLINE:#
            is_new_line = True
            log.debug(current_line_content)
            instructions[current_ins_address] = current_line_content#记录指令下标和指令内容到instructions数组中
            idx = mapping_non_push_instruction(current_line_content, current_ins_address, idx, positions, length) if source_map else None#获得最新的source_map.positions的下标
            current_line_content = ""
            continue
        elif tok_type == NAME:
            if tok_string == "JUMPDEST":#metadata to annotate possible jump destinations，JUMPDEST比较特殊，不需要记录
                if last_ins_address not in end_ins_dict:#如果JUMPDEST上一个指令不是某个块的最后一个指令则添加到end_ins_dict，作为当前快的最后一个指令
                    end_ins_dict[current_block] = last_ins_address#记录当前最后一个块的指令
                current_block = current_ins_address#更新当前检索的块到跳转到的块
                is_new_block = False
            elif tok_string == "STOP" or tok_string == "RETURN" or tok_string == "SUICIDE" or tok_string == "REVERT" or tok_string == "ASSERTFAIL":
                jump_type[current_block] = "terminal"#结束块
                end_ins_dict[current_block] = current_ins_address
            elif tok_string == "JUMP":
                jump_type[current_block] = "unconditional"
                end_ins_dict[current_block] = current_ins_address
                is_new_block = True
            elif tok_string == "JUMPI":
                jump_type[current_block] = "conditional"
                end_ins_dict[current_block] = current_ins_address#
                is_new_block = True
            elif tok_string.startswith('PUSH', 0):
                wait_for_push = True#
            is_new_line = False#检索到NAME，不是NEWLINE
        if tok_string != "=" and tok_string != ">":
            current_line_content += tok_string + " "

    if current_block not in end_ins_dict:
        log.debug("current block: %d", current_block)
        log.debug("last line: %d", current_ins_address)
        end_ins_dict[current_block] = current_ins_address#第一行current_ins_address：0，current_block：0

    if current_block not in jump_type:#jump_type：[]
        jump_type[current_block] = "terminal"#{0: 'terminal'}

    for key in end_ins_dict:#遍历每个块对应的结束语句，key：0，end_ins_dict：{0: 0}，key not in jump_type：false
        if key not in jump_type:
            jump_type[key] = "falls_to"


def construct_bb():
    global vertices
    global edges
    sorted_addresses = sorted(instructions.keys())#第一个基本块：instructions：{}
    size = len(sorted_addresses)#第一个基本块：size：0，end_ins_dict：{0：0}
    for key in end_ins_dict:#end_ins_dict某种指令的开始和结束字符位置，key是开始字符位置，end_address是结束字符位置
        end_address = end_ins_dict[key]
        block = BasicBlock(key, end_address)#第一个基本块：key：0，end_address：0
        if key not in instructions:#0不在instructions中
            continue
        block.add_instruction(instructions[key])#往基本块中添加指令
        i = sorted_addresses.index(key) + 1
        while i < size and sorted_addresses[i] <= end_address:
            block.add_instruction(instructions[sorted_addresses[i]])
            i += 1
        block.set_block_type(jump_type[key])#jump_type中，{块的开始位置：块的跳转类型}
        vertices[key] = block#vertices中，{块的开始位置：快对象}
        edges[key] = []#构造以该块为起点的边数组，{块的开始位置：块能跳转到的块的开始位置}


def construct_static_edges():
    add_falls_to()  # these edges are static


def add_falls_to():#构造静态边
    global vertices
    global edges
    key_list = sorted(jump_type.keys())#jump_type是{0: 'terminal'}，key_list是[0]
    length = len(key_list)
    for i, key in enumerate(key_list):#还有后续块的块，不是无条件跳转的块，包含有条件跳转的块，需要设置条件不满足时的跳转，称为静态的边的构造
        if jump_type[key] != "terminal" and jump_type[key] != "unconditional" and i+1 < length:#不是结束语句，不是无条件跳转语句，如果i+1>=length，则是一个块最后一条语句，即不是最后一条语句
            target = key_list[i+1]
            edges[key].append(target)#添加有向边，key->target
            vertices[key].set_falls_to(target)#默认的跳转

def get_init_global_state(path_conditions_and_vars):#path_conditions_and_vars：{"path_condition" : []}
    global_state = {"balance" : {}, "pc": 0}
    init_is = init_ia = deposited_value = sender_address = receiver_address = gas_price = origin = currentCoinbase = currentNumber = currentDifficulty = currentGasLimit = callData = None#空对象

    if global_params.INPUT_STATE:#从state.json中初始化全局变量，global_params.INPUT_STATE：0
        with open('state.json') as f:
            state = json.loads(f.read())
            if state["Is"]["balance"]:
                init_is = int(state["Is"]["balance"], 16)#将"0x..."转为十进制
            if state["Ia"]["balance"]:
                init_ia = int(state["Ia"]["balance"], 16)
            if state["exec"]["value"]:
                deposited_value = 0
            if state["Is"]["address"]:
                sender_address = int(state["Is"]["address"], 16)
            if state["Ia"]["address"]:
                receiver_address = int(state["Ia"]["address"], 16)
            if state["exec"]["gasPrice"]:
                gas_price = int(state["exec"]["gasPrice"], 16)
            if state["exec"]["origin"]:
                origin = int(state["exec"]["origin"], 16)
            if state["env"]["currentCoinbase"]:
                currentCoinbase = int(state["env"]["currentCoinbase"], 16)
            if state["env"]["currentNumber"]:
                currentNumber = int(state["env"]["currentNumber"], 16)
            if state["env"]["currentDifficulty"]:
                currentDifficulty = int(state["env"]["currentDifficulty"], 16)
            if state["env"]["currentGasLimit"]:
                currentGasLimit = int(state["env"]["currentGasLimit"], 16)

    # for some weird reason these 3 vars are stored in path_conditions insteaad of global_state
    else:#如果没有state.json则初始化位向量
        sender_address = BitVec("Is", 256)
        receiver_address = BitVec("Ia", 256)
        deposited_value = BitVec("Iv", 256)
        init_is = BitVec("init_Is", 256)
        init_ia = BitVec("init_Ia", 256)

    path_conditions_and_vars["Is"] = sender_address#"Is"
    path_conditions_and_vars["Ia"] = receiver_address#"Ia"
    path_conditions_and_vars["Iv"] = deposited_value#"Iv"

    constraint = (deposited_value >= BitVecVal(0, 256))#constraint:Iv>=0
    path_conditions_and_vars["path_condition"].append(constraint)
    constraint = (init_is >= deposited_value)#init_Is >= Iv
    path_conditions_and_vars["path_condition"].append(constraint)
    constraint = (init_ia >= BitVecVal(0, 256))#init_Ia >= 0
    path_conditions_and_vars["path_condition"].append(constraint)#path_conditions_and_vars:{'Ia': Ia, 'path_condition': [Iv >= 0, init_Is >= Iv, init_Ia >= 0], 'Is': Is, 'Iv': Iv}

    # update the balances of the "caller" and "callee"

    global_state["balance"]["Is"] = (init_is - deposited_value)#init_Is - Iv
    global_state["balance"]["Ia"] = (init_ia + deposited_value)#init_Ia + Iv

    if not gas_price:#如果没有燃气价格
        new_var_name = gen.gen_gas_price_var()#创建一个变量，返回的是字符串类型"tx.gasprice"
        gas_price = BitVec(new_var_name, 256)#tx.gasprice
        path_conditions_and_vars[new_var_name] = gas_price#{'Ia': Ia, 'path_condition': [Iv >= 0, init_Is >= Iv, init_Ia >= 0], 'Is': Is, 'tx.gasprice': tx.gasprice, 'Iv': Iv}

    if not origin:
        new_var_name = gen.gen_origin_var()
        origin = BitVec(new_var_name, 256)#'tx.origin'
        path_conditions_and_vars[new_var_name] = origin#{'path_condition': [Iv >= 0, init_Is >= Iv, init_Ia >= 0], 'tx.origin': tx.origin, 'Is': Is, 'Iv': Iv, 'tx.gasprice': tx.gasprice, 'Ia': Ia}

    if not currentCoinbase:#铸币
        new_var_name = "IH_c"
        currentCoinbase = BitVec(new_var_name, 256)#IH_c
        path_conditions_and_vars[new_var_name] = currentCoinbase#{'path_condition': [Iv >= 0, init_Is >= Iv, init_Ia >= 0], 'tx.origin': tx.origin, 'Is': Is, 'Iv': Iv, 'tx.gasprice': tx.gasprice, 'IH_c': IH_c, 'Ia': Ia}

    if not currentNumber:
        new_var_name = "IH_i"
        currentNumber = BitVec(new_var_name, 256)
        path_conditions_and_vars[new_var_name] = currentNumber#{'path_condition': [Iv >= 0, init_Is >= Iv, init_Ia >= 0], 'tx.origin': tx.origin, 'Is': Is, 'Iv': Iv, 'tx.gasprice': tx.gasprice, 'IH_c': IH_c, 'Ia': Ia, 'IH_i': IH_i}

    if not currentDifficulty:
        new_var_name = "IH_d"
        currentDifficulty = BitVec(new_var_name, 256)
        path_conditions_and_vars[new_var_name] = currentDifficulty#{'path_condition': [Iv >= 0, init_Is >= Iv, init_Ia >= 0], 'tx.origin': tx.origin, 'Is': Is, 'Iv': Iv, 'IH_d': IH_d, 'tx.gasprice': tx.gasprice, 'IH_c': IH_c, 'Ia': Ia, 'IH_i': IH_i}

    if not currentGasLimit:
        new_var_name = "IH_l"
        currentGasLimit = BitVec(new_var_name, 256)
        path_conditions_and_vars[new_var_name] = currentGasLimit#{'path_condition': [Iv >= 0, init_Is >= Iv, init_Ia >= 0], 'tx.origin': tx.origin, 'Is': Is, 'Iv': Iv, 'IH_d': IH_d, 'tx.gasprice': tx.gasprice, 'IH_c': IH_c, 'Ia': Ia, 'IH_l': IH_l, 'IH_i': IH_i}

    new_var_name = "IH_s"
    currentTimestamp = BitVec(new_var_name, 256)
    path_conditions_and_vars[new_var_name] = currentTimestamp#{'path_condition': [Iv >= 0, init_Is >= Iv, init_Ia >= 0], 'IH_s': IH_s, 'tx.origin': tx.origin, 'Is': Is, 'Iv': Iv, 'IH_d': IH_d, 'tx.gasprice': tx.gasprice, 'IH_c': IH_c, 'Ia': Ia, 'IH_l': IH_l, 'IH_i': IH_i}

    # the state of the current current contract 开始初始化global state
    if "Ia" not in global_state:
        global_state["Ia"] = {}
    global_state["miu_i"] = 0
    global_state["value"] = deposited_value
    global_state["sender_address"] = sender_address
    global_state["receiver_address"] = receiver_address
    global_state["gas_price"] = gas_price
    global_state["origin"] = origin
    global_state["currentCoinbase"] = currentCoinbase
    global_state["currentTimestamp"] = currentTimestamp
    global_state["currentNumber"] = currentNumber
    global_state["currentDifficulty"] = currentDifficulty
    global_state["currentGasLimit"] = currentGasLimit#{'origin': tx.origin, 'gas_price': tx.gasprice, 'currentTimestamp': IH_s, 'miu_i': 0, 'currentCoinbase': IH_c, 'value': Iv, 'sender_address': Is, 'pc': 0, 'currentDifficulty': IH_d, 'Ia': {}, 'receiver_address': Ia, 'balance': {'Ia': init_Ia + Iv, 'Is': init_Is - Iv}, 'currentNumber': IH_i}

    return global_state


def full_sym_exec():
    # executing, starting from beginning
    path_conditions_and_vars = {"path_condition" : []}#路径条件
    global_state = get_init_global_state(path_conditions_and_vars)#使用初始条件
    analysis = init_analysis()#初始化analysis字典,{'sstore': {}, 'money_flow': [('Is', 'Ia', 'Iv')], 'sload': [], 'reentrancy_bug': [], 'money_concurrency_bug': [], 'gas_mem': 0, 'gas': 0, 'time_dependency_bug': {}}
    params = Parameter(path_conditions_and_vars=path_conditions_and_vars, global_state=global_state, analysis=analysis)#使用'analysis'作为对象的属性名，通过iteritems()获取attr,default中的attr
    return sym_exec_block(params)#


# Symbolically executing a block from the start address 从起始地址开始符号执行一个块
def sym_exec_block(params):#符号执行一个块
    global solver
    global visited_edges
    global money_flow_all_paths
    global data_flow_all_paths
    global path_conditions
    global global_problematic_pcs
    global all_gs
    global results
    global source_map

    block = params.block
    pre_block = params.pre_block#初始值0
    visited = params.visited
    depth = params.depth
    stack = params.stack
    mem = params.mem
    memory = params.memory
    global_state = params.global_state
    sha3_list = params.sha3_list
    path_conditions_and_vars = params.path_conditions_and_vars
    analysis = params.analysis
    models = params.models
    calls = params.calls
    func_call = params.func_call#将对象的初始值传入变量

    Edge = namedtuple("Edge", ["v1", "v2"]) # Factory Function for tuples is used as dictionary key元组的Factory Function用作字典键
    if block < 0:#无传参初始化后block：0，创建一个名为Edge的nametuple对象，域名分别为v1，v2
        log.debug("UNKNOWN JUMP ADDRESS. TERMINATING THIS PATH")#当前block地址非法
        return ["ERROR"]

    if global_params.DEBUG_MODE:
        print("Reach block address " + hex(block))
        print("STACK: " + str(stack))

    current_edge = Edge(pre_block, block)#获得当前要执行的边，current_edge=Edge(v1=0, v2=0)
    if visited_edges.has_key(current_edge):#如果当前边已经遍历过，visited_edges：{}
        updated_count_number = visited_edges[current_edge] + 1#则更新当前边的计数
        visited_edges.update({current_edge: updated_count_number})
    else:
        visited_edges.update({current_edge: 1})#否则初始化当前边，visited_edges：{Edge(v1=0, v2=0): 1}

    if visited_edges[current_edge] > global_params.LOOP_LIMIT:#如果当前边的执行次数过高则终止
        if global_params.DEBUG_MODE:
            print("Overcome a number of loop limit. Terminating this path ...")#克服多个循环限制。终止这条道路
        return stack

    current_gas_used = analysis["gas"]#已经模拟的需要使用的gas，初始化analysis["gas"]：0
    if current_gas_used > global_params.GAS_LIMIT:#初始值global_params.GAS_LIMIT：4000000
        if global_params.DEBUG_MODE:
            print("Run out of gas. Terminating this path ... ")
        return stack

    # Execute every instruction, one at a time
    try:
        block_ins = vertices[block].get_instructions()#当前边的指令列表，block：0，vertices[block]：<basicblock.BasicBlock instance at 0x7fc7ef157518>
    except KeyError:#vertices[block].get_instructions()：['PUSH1 0x60 ', 'PUSH1 0x40 ', 'MSTORE ', 'PUSH1 0x04 ', 'CALLDATASIZE ', 'LT ', 'PUSH2 0x004c ', 'JUMPI ']
        if global_params.DEBUG_MODE:
            print("This path results in an exception, possibly an invalid jump address")#如果当前块的地址出错
        return ["ERROR"]

    for instr in block_ins:#遍历块的每一个指令，如'PUSH1 0x60 '
        if global_params.DEBUG_MODE:
            print(str(global_state["pc"])+" \t "+str(instr))#当前的pc和具体指令
        params.instr = instr#将传入的parameter对象的一个属性设置为intr，初始值为空字符串
        sym_exec_ins(params)
    if global_params.DEBUG_MODE:
        print("")

    # Mark that this basic block in the visited blocks将这个基本块标记在已访问块中
    visited.append(block)
    depth += 1

    reentrancy_all_paths.append(analysis["reentrancy_bug"])
    if analysis["money_flow"] not in money_flow_all_paths:#并发的提款等金融操作的bug
        global_problematic_pcs["money_concurrency_bug"].append(analysis["money_concurrency_bug"])
        money_flow_all_paths.append(analysis["money_flow"])
        path_conditions.append(path_conditions_and_vars["path_condition"])
        global_problematic_pcs["time_dependency_bug"].append(analysis["time_dependency_bug"])
        all_gs.append(copy_global_values(global_state))
    if global_params.DATA_FLOW:
        if analysis["sload"] not in data_flow_all_paths[0]:
            data_flow_all_paths[0].append(analysis["sload"])
        if analysis["sstore"] not in data_flow_all_paths[1]:
            data_flow_all_paths[1].append(analysis["sstore"])

    # Go to next Basic Block(s)
    if jump_type[block] == "terminal" or depth > global_params.DEPTH_LIMIT:
        global total_no_of_paths
        global no_of_test_cases

        if global_params.DEBUG_MODE:
            if depth > global_params.DEPTH_LIMIT:
                print "!!! DEPTH LIMIT EXCEEDED !!!"

        total_no_of_paths += 1#执行到结束块则路径数+1

        if global_params.GENERATE_TEST_CASES:
            try:
                model = solver.model()
                no_of_test_cases += 1
                filename = "test%s.otest" % no_of_test_cases
                with open(filename, 'w') as f:
                    for variable in model.decls():
                        f.write(str(variable) + " = " + str(model[variable]) + "\n")
                if os.stat(filename).st_size == 0:
                    os.remove(filename)
                    no_of_test_cases -= 1
            except Exception as e:
                pass

        if global_params.DEBUG_MODE:
            print "Termintating path: "+str(total_no_of_paths)
            print "Depth: "+str(depth)
            print ""

        display_analysis(analysis)#展示money_flow，analysis：{'sstore': {}, 'money_flow': [('Is', 'Ia', 'Iv')], 'money_concurrency_bug': [], 'gas': 742, 'sload': [0], 'reentrancy_bug': [], 'gas_mem': 9, 'time_dependency_bug': {3: 12, 4: 319, 5: 332, 6: 423, 7: 436}}
        if global_params.UNIT_TEST == 1:
            compare_stack_unit_test(stack)
        if global_params.UNIT_TEST == 2 or global_params.UNIT_TEST == 3:
            compare_storage_and_gas_unit_test(global_state, analysis)

    elif jump_type[block] == "unconditional":  # executing "JUMP"
        successor = vertices[block].get_jump_target()
        new_params = params.copy()#
        new_params.depth = depth
        new_params.block = successor#当前的块
        new_params.pre_block = block
        new_params.global_state["pc"] = successor
        if source_map:
            source_code = source_map.find_source_code(global_state["pc"])
            if source_code in source_map.func_call_names:
                new_params.func_call = global_state["pc"]
        sym_exec_block(new_params)
    elif jump_type[block] == "falls_to":  # just follow to the next basic block
        successor = vertices[block].get_falls_to()#前往jump条件不满足时跳转的块
        new_params = params.copy()
        new_params.depth = depth
        new_params.block = successor
        new_params.pre_block = block
        new_params.global_state["pc"] = successor
        sym_exec_block(new_params)
    elif jump_type[block] == "conditional":  # executing "JUMPI"
        # A choice point, we proceed with depth first search

        branch_expression = vertices[block].get_branch_expression()#分支表达式

        if global_params.DEBUG_MODE:
            print("Branch expression: " + remove_line_break_space(branch_expression))

        solver.push()  # SET A BOUNDARY FOR SOLVER#添加分支表达式到z3
        solver.add(branch_expression)

        isLeftBranchFeasible = True

        try:
            try:
                if solver.check() == unsat:#如果左边分支无解，则isLeftBranchFeasible设置为false
                    isLeftBranchFeasible = False
            except:
                isLeftBranchFeasible = False
            if isLeftBranchFeasible:#如果有解
                left_branch = vertices[block].get_jump_target()#left_branch是当前块跳转到的块
                new_params = params.copy()
                new_params.depth = depth
                new_params.block = left_branch
                new_params.pre_block = block
                new_params.global_state["pc"] = left_branch
                new_params.path_conditions_and_vars["path_condition"].append(branch_expression)
                last_idx = len(new_params.path_conditions_and_vars["path_condition"]) - 1
                new_params.analysis["time_dependency_bug"][last_idx] = global_state["pc"]
                try:
                    new_params.models.append(solver.model())
                except:
                    pass
                sym_exec_block(new_params)
            elif global_params.DEBUG_MODE:
                print("LEFT BRANCH IS INFEASIBLE")
        except Exception as e:
            log_file.write(str(e))
            if global_params.DEBUG_MODE:
                traceback.print_exc()
            if not global_params.IGNORE_EXCEPTIONS:
                if str(e) == "timeout":
                    raise e

        solver.pop()  # POP SOLVER CONTEXT弹出原来的要求解的表达式

        solver.push()  # SET A BOUNDARY FOR SOLVER
        negated_branch_expression = Not(branch_expression)#翻转分支表达式再计算,
#branch_expression:If(code_size_Concat(0, Extract(159, 0, Ia_store_0)) == 0,
#    0,
#    1) !=
# 0
#negated_branch_expression：
# Not(If(code_size_Concat(0, Extract(159, 0, Ia_store_0)) == 0,
    #    0,
    #    1) !=
    # 0)
        solver.add(negated_branch_expression)

        if global_params.DEBUG_MODE:
            print("Negated branch expression: " + remove_line_break_space(negated_branch_expression))

        isRightBranchFeasible = True

        try:
            try:
                if not isLeftBranchFeasible and solver.check() == unsat:
                    isRightBranchFeasible = False
            except:
                isRightBranchFeasible = False
            if isRightBranchFeasible:#vertices[block]：<basicblock.BasicBlock instance at 0x7fc7ef1919e0>，block337
                right_branch = vertices[block].get_falls_to()#左分支是满足条件时跳转的，右分支是不满足条件时跳转的
                new_params = params.copy()
                new_params.depth = depth
                new_params.block = right_branch
                new_params.pre_block = block
                new_params.global_state["pc"] = right_branch
                new_params.path_conditions_and_vars["path_condition"].append(negated_branch_expression)
                last_idx = len(new_params.path_conditions_and_vars["path_condition"]) - 1
                new_params.analysis["time_dependency_bug"][last_idx] = global_state["pc"]
                try:
                    new_params.models.append(solver.model())
                except:
                    pass
                sym_exec_block(new_params)
            elif global_params.DEBUG_MODE:#不可行
                print("RIGHT BRANCH IS INFEASIBLE")
        except Exception as e:
            log_file.write(str(e))
            if global_params.DEBUG_MODE:
                traceback.print_exc()
            if not global_params.IGNORE_EXCEPTIONS:
                if str(e) == "timeout":
                    raise e

        solver.pop()  # POP SOLVER CONTEXT z3解析器弹出上下文
        updated_count_number = visited_edges[current_edge] - 1#visited_edges[current_edge]：1，current_edge：Edge(v1=337, v2=428)
        visited_edges.update({current_edge: updated_count_number})#updated_count_number=0，update后{Edge(v1=428, v2=437): 1, Edge(v1=76, v2=324): 1, Edge(v1=337, v2=428): 0, Edge(v1=428, v2=441): 1, Edge(v1=324, v2=337): 1, Edge(v1=0, v2=0): 1, Edge(v1=0, v2=76): 1}
    else:
        updated_count_number = visited_edges[current_edge] - 1
        visited_edges.update({current_edge: updated_count_number})
        raise Exception('Unknown Jump-Type')

# Symbolically executing an instruction
def sym_exec_ins(params):#ethervm.io的所有单条指令
    global visited_pcs
    global solver
    global vertices
    global edges
    global source_map
    global validator
    global g_timeout

    if g_timeout:
        raise Exception("timeout")

    start = params.block#设置当前指令执行时的环境，start：0
    instr = params.instr#取出指令进行遍历，instr：'PUSH1 0x60 '
    stack = params.stack#[]
    mem = params.mem#{}
    memory = params.memory#[]
    global_state = params.global_state#{'origin': tx.origin, 'gas_price': tx.gasprice, 'currentTimestamp': IH_s, 'miu_i': 0, 'currentCoinbase': IH_c, 'value': Iv, 'sender_address': Is, 'pc': 0, 'currentDifficulty': IH_d, 'Ia': {}, 'currentGasLimit': IH_l, 'receiver_address': Ia, 'balance': {'Ia': init_Ia + Iv, 'Is': init_Is - Iv}, 'currentNumber': IH_i}
    sha3_list = params.sha3_list#{}
    path_conditions_and_vars = params.path_conditions_and_vars#{'path_condition': [Iv >= 0, init_Is >= Iv, init_Ia >= 0], 'IH_s': IH_s, 'tx.origin': tx.origin, 'Is': Is, 'Iv': Iv, 'IH_d': IH_d, 'tx.gasprice': tx.gasprice, 'IH_c': IH_c, 'Ia': Ia, 'IH_l': IH_l, 'IH_i': IH_i}
    analysis = params.analysis#{'sstore': {}, 'money_flow': [('Is', 'Ia', 'Iv')], 'sload': [], 'reentrancy_bug': [], 'money_concurrency_bug': [], 'gas_mem': 0, 'gas': 0, 'time_dependency_bug': {}}
    models = params.models#[]
    calls = params.calls#[]
    func_call = params.func_call#-1

    visited_pcs.add(global_state["pc"])#0;visited_pcs:set([0])

    instr_parts = str.split(instr, ' ')#['PUSH1', '0x60', '']

    previous_stack = copy_all(stack)[0]#copy_all(stack)[0]:[[]]
    previous_pc = global_state["pc"]#0

    #if instr_parts[0] == "INVALID":
    #    return
    #elif instr_parts[0] == "ASSERTFAIL":
    #    if source_map:
    #        source_code = source_map.find_source_code(global_state["pc"])
    #        if "assert" in source_code:
    #            global_problematic_pcs["assertion_failure"].append(Assertion(global_state["pc"], models[-1]))
    #        elif func_call != -1:
    #            global_problematic_pcs["assertion_failure"].append(Assertion(func_call, models[-1]))
    #    else:
    #        global_problematic_pcs["assertion_failure"].append(Assertion(global_state["pc"], models[-1]))
    #    return

    # collecting the analysis result by calling this skeletal function
    # this should be done before symbolically executing the instruction,
    # since SE will modify the stack and mem
    #通过调用这个骨架函数来收集分析结果
    #上述步骤应该在符号执行指令之前执行
    #因为SE（符号执行）将修改堆栈和内存
    update_analysis(analysis, instr_parts[0], stack, mem, global_state, path_conditions_and_vars, solver)
    #更新后：{'sstore': {}, 'money_flow': [('Is', 'Ia', 'Iv')], 'sload': [], 'reentrancy_bug': [], 'money_concurrency_bug': [], 'gas_mem': 0, 'gas': 3, 'time_dependency_bug': {}}
    if instr_parts[0] == "CALL" and analysis["reentrancy_bug"] and analysis["reentrancy_bug"][-1]:#instr_parts[0]是call即指令是call
        global_problematic_pcs["reentrancy_bug"].append(global_state["pc"])

    log.debug("==============================")
    log.debug("EXECUTING: " + instr)

    #
    #  0s: Stop and Arithmetic Operations
    #
    if instr_parts[0] == "STOP":#stop和算数操作
        global_state["pc"] = global_state["pc"] + 1
        #return
    elif instr_parts[0] == "ADD":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            instruction_object = InstructionObject(instr_parts[0], [], [])
            first = stack.pop(0)
            second = stack.pop(0)
            instruction_object.data_in = [first, second]
            # Type conversion is needed when they are mismatched转换类型
            if isReal(first) and isSymbolic(second):#统一转换为bitvec
                first = BitVecVal(first, 256)
                computed = first + second
            elif isSymbolic(first) and isReal(second):
                second = BitVecVal(second, 256)
                computed = first + second
            else:
                # both are real and we need to manually modulus with 2 ** 256
                # if both are symbolic z3 takes care of modulus automatically如果两者都是z3规定的符号，则z3会自行转换
                computed = (first + second) % (2 ** 256)#已经转换完成
            computed = simplify(computed) if is_expr(computed) else computed#如果是z3则使用simplify否则直接赋值
            instruction_object.data_out = [computed]#添加计算结果
            stack.insert(0, computed)#更新该条指令运行后的栈状态
            # Check for addition overflow检查加法溢出
            if is_input_tainted(instruction_object):
                addition_overflow_check(first, second, analysis, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "MUL":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            instruction_object = InstructionObject(instr_parts[0], [], [])
            first = stack.pop(0)
            second = stack.pop(0)
            instruction_object.data_in = [first, second]
            if isReal(first) and isSymbolic(second):
                first = BitVecVal(first, 256)
            elif isSymbolic(first) and isReal(second):
                second = BitVecVal(second, 256)
            computed = first * second & UNSIGNED_BOUND_NUMBER
            computed = simplify(computed) if is_expr(computed) else computed
            instruction_object.data_out = [computed]
            stack.insert(0, computed)
            # Check for multiplication overflow
            if is_input_tainted(instruction_object):
                multiplication_overflow_check(first, second, analysis, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "SUB":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            instruction_object = InstructionObject(instr_parts[0], [], [])
            first = stack.pop(0)
            second = stack.pop(0)
            instruction_object.data_in = [first, second]
            if isReal(first) and isSymbolic(second):
                first = BitVecVal(first, 256)
                computed = first - second
            elif isSymbolic(first) and isReal(second):
                second = BitVecVal(second, 256)
                computed = first - second
            else:
                computed = (first - second) % (2 ** 256)
            computed = simplify(computed) if is_expr(computed) else computed
            instruction_object.data_out = [computed]
            stack.insert(0, computed)
            # Check for subtraction underflow
            if is_input_tainted(instruction_object):
                subtraction_underflow_check(first, second, analysis, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "DIV":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            instruction_object = InstructionObject(instr_parts[0], [], [])
            first = stack.pop(0)
            second = stack.pop(0)
            instruction_object.data_in = [first, second]
            if isAllReal(first, second):
                if second == 0:
                    computed = 0
                else:
                    first = to_unsigned(first)
                    second = to_unsigned(second)
                    computed = first / second
            else:
                first = to_symbolic(first)
                second = to_symbolic(second)
                solver.push()
                solver.add(Not(second == 0))
                if check_solver(solver) == unsat:
                    computed = 0
                else:
                    computed = UDiv(first, second)
                solver.pop()
            computed = simplify(computed) if is_expr(computed) else computed
            instruction_object.data_out = [computed]
            stack.insert(0, computed)
            # Check for signedness conversion
            check_signedness_conversion(computed, type_information, False, False, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
            # Check for unsigned division
            if is_input_tainted(instruction_object):
                unsigned_division_check(second, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "SDIV":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            instruction_object = InstructionObject(instr_parts[0], [], [])
            first = stack.pop(0)
            second = stack.pop(0)
            instruction_object.data_in = [first, second]
            if isAllReal(first, second):
                first = to_signed(first)
                second = to_signed(second)
                if second == 0:
                    computed = 0
                elif first == -2**255 and second == -1:
                    computed = -2**255
                else:
                    sign = -1 if (first / second) < 0 else 1
                    computed = sign * ( abs(first) / abs(second) )
            else:
                first = to_symbolic(first)
                second = to_symbolic(second)
                solver.push()
                solver.add(Not(second == 0))
                if check_solver(solver) == unsat:
                    computed = 0
                else:
                    solver.push()
                    solver.add( Not( And(first == -2**255, second == -1 ) ))
                    if check_solver(solver) == unsat:
                        computed = -2**255
                    else:
                        s = Solver()
                        s.set("timeout", global_params.TIMEOUT)
                        s.add(first / second < 0)
                        sign = -1 if check_solver(s) == sat else 1
                        z3_abs = lambda x: If(x >= 0, x, -x)
                        first = z3_abs(first)
                        second = z3_abs(second)
                        computed = sign * (first / second)
                    solver.pop()
                solver.pop()
            computed = simplify(computed) if is_expr(computed) else computed
            instruction_object.data_out = [computed]
            stack.insert(0, computed)
            # Check for signedness conversion
            check_signedness_conversion(computed, type_information, False, True, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
            # Check for signed division
            if is_input_tainted(instruction_object):
                signed_division_check(first, second, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "MOD":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            instruction_object = InstructionObject(instr_parts[0], [], [])
            first = stack.pop(0)
            second = stack.pop(0)
            instruction_object.data_in = [first, second]
            if isAllReal(first, second):
                if second == 0:
                    computed = 0
                else:
                    first = to_unsigned(first)
                    second = to_unsigned(second)
                    computed = first % second & UNSIGNED_BOUND_NUMBER
            else:
                first = to_symbolic(first)
                second = to_symbolic(second)
                solver.push()
                solver.add(Not(second == 0))
                if check_solver(solver) == unsat:
                    # it is provable that second is indeed equal to zero
                    computed = 0
                else:
                    computed = URem(first, second)
                solver.pop()
            computed = simplify(computed) if is_expr(computed) else computed
            instruction_object.data_out = [computed]
            stack.insert(0, computed)
            # Check for signed conversion
            check_signedness_conversion(computed, type_information, False, False, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
            # Check for modulo zero
            if is_input_tainted(instruction_object):
                modulo_check(second, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "SMOD":#singed modulus有符号的取模
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            instruction_object = InstructionObject(instr_parts[0], [], [])
            first = stack.pop(0)
            second = stack.pop(0)
            instruction_object.data_in = [first, second]
            if isAllReal(first, second):
                if second == 0:
                    computed = 0
                else:
                    first = to_signed(first)
                    second = to_signed(second)
                    sign = -1 if first < 0 else 1
                    computed = sign * (abs(first) % abs(second))
            else:
                first = to_symbolic(first)
                second = to_symbolic(second)
                solver.push()
                solver.add(Not(second == 0))
                if check_solver(solver) == unsat:
                    # it is provable that second is indeed equal to zero 可以证明第二个确实等于零
                    computed = 0
                else:
                    solver.push()
                    solver.add(first < 0) # check sign of first element
                    sign = BitVecVal(-1, 256) if check_solver(solver) == sat \
                        else BitVecVal(1, 256)
                    solver.pop()
                    z3_abs = lambda x: If(x >= 0, x, -x)
                    first = z3_abs(first)
                    second = z3_abs(second)
                    computed = sign * (first % second)
                solver.pop()
            computed = simplify(computed) if is_expr(computed) else computed
            instruction_object.data_out = [computed]
            stack.insert(0, computed)
            # Check for signedness conversion
            check_signedness_conversion(computed, type_information, False, True, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
            # Check for modulo zero
            if is_input_tainted(instruction_object):
                modulo_check(second, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "ADDMOD":
        if len(stack) > 2:
            global_state["pc"] = global_state["pc"] + 1
            instruction_object = InstructionObject(instr_parts[0], [], [])
            first = stack.pop(0)
            second = stack.pop(0)
            third = stack.pop(0)
            instruction_object.data_in = [first, second, third]
            if isAllReal(first, second, third):
                if third == 0:
                    computed = 0
                else:
                    computed = (first + second) % third
            else:
                first = to_symbolic(first)
                second = to_symbolic(second)
                third = to_symbolic(third)
                solver.push()
                solver.add(Not(third == 0))
                if check_solver(solver) == unsat:
                    computed = 0
                else:
                    first = ZeroExt(256, first)
                    second = ZeroExt(256, second)
                    third = ZeroExt(256, third)
                    computed = (first + second) % third
                    computed = Extract(255, 0, computed)
                solver.pop()
            computed = simplify(computed) if is_expr(computed) else computed
            instruction_object.data_out = [computed]
            stack.insert(0, computed)
            # Check for modulo zero
            if is_input_tainted(instruction_object):
                modulo_check(third, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "MULMOD":
        if len(stack) > 2:
            global_state["pc"] = global_state["pc"] + 1
            instruction_object = InstructionObject(instr_parts[0], [], [])
            first = stack.pop(0)
            second = stack.pop(0)
            third = stack.pop(0)
            instruction_object.data_in = [first, second, third]
            if isAllReal(first, second, third):
                if third == 0:
                    computed = 0
                else:
                    computed = (first * second) % third
            else:
                first = to_symbolic(first)
                second = to_symbolic(second)
                third = to_symbolic(third)
                solver.push()
                solver.add(Not(third == 0))
                if check_solver(solver) == unsat:
                    computed = 0
                else:
                    first = ZeroExt(256, first)
                    second = ZeroExt(256, second)
                    third = ZeroExt(256, third)
                    computed = URem(first * second, third)
                    computed = Extract(255, 0, computed)
                solver.pop()
            computed = simplify(computed) if is_expr(computed) else computed
            instruction_object.data_out = [computed]
            stack.insert(0, computed)
            # Check for modulo zero
            if is_input_tainted(instruction_object):
                modulo_check(third, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "EXP":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            base = stack.pop(0)
            exponent = stack.pop(0)
            # Type conversion is needed when they are mismatched
            if isAllReal(base, exponent):
                computed = pow(base, exponent, 2**256)
            else:
                # The computed value is unknown, this is because power is
                # not supported in bit-vector theory
                new_var_name = gen.gen_arbitrary_var()
                computed = BitVec(new_var_name, 256)
            computed = simplify(computed) if is_expr(computed) else computed
            stack.insert(0, computed)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "SIGNEXTEND":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            instruction_object = InstructionObject(instr_parts[0], [], [])
            first = stack.pop(0)
            second = stack.pop(0)
            instruction_object.data_in = [first, second]
            if isAllReal(first, second):
                if first >= 32 or first < 0:
                    computed = second
                else:
                    signbit_index_from_right = 8 * first + 7
                    if second & (1 << signbit_index_from_right):
                        computed = second | (2 ** 256 - (1 << signbit_index_from_right))
                    else:
                        computed = second & ((1 << signbit_index_from_right) - 1 )
            else:
                first = to_symbolic(first)
                second = to_symbolic(second)
                solver.push()
                solver.add(Not(Or(first >= 32, first < 0)))
                if check_solver(solver) == unsat:
                    computed = second
                else:
                    signbit_index_from_right = 8 * first + 7
                    solver.push()
                    solver.add(second & (1 << signbit_index_from_right) == 0)
                    if check_solver(solver) == unsat:
                        computed = second | (2 ** 256 - (1 << signbit_index_from_right))
                    else:
                        computed = second & ((1 << signbit_index_from_right) - 1)
                    solver.pop()
                solver.pop()
            computed = simplify(computed) if is_expr(computed) else computed
            instruction_object.data_out = [computed]
            stack.insert(0, computed)
            # Check for signedness conversion
            check_signedness_conversion(computed, type_information, True, True, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
            # Check for width conversion
            if is_input_tainted(instruction_object):
                conversion = check_width_conversion(first, second, computed, instruction_object, vertices[params.block], path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
                if conversion:
                    if not computed in width_conversions:
                        width_conversions.append(computed)
        else:
            raise ValueError('STACK underflow')
    #
    #  10s: Comparison and Bitwise Logic Operations,10是ethervm.io的值
    #
    elif instr_parts[0] == "LT":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            instruction_object = InstructionObject(instr_parts[0], [], [])
            first = stack.pop(0)
            second = stack.pop(0)
            instruction_object.data_in = [first, second]
            if isAllReal(first, second):
                first = to_unsigned(first)
                second = to_unsigned(second)
                if first < second:
                    computed = 1
                else:
                    computed = 0
            else:
                computed = If(ULT(first, second), BitVecVal(1, 256), BitVecVal(0, 256))
            computed = simplify(computed) if is_expr(computed) else computed
            instruction_object.data_out = [computed]
            stack.insert(0, computed)
            # Check for signedness conversion
            check_signedness_conversion(computed, type_information, False, False, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "GT":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            instruction_object = InstructionObject(instr_parts[0], [], [])
            first = stack.pop(0)
            second = stack.pop(0)
            instruction_object.data_in = [first, second]
            if isAllReal(first, second):
                first = to_unsigned(first)
                second = to_unsigned(second)
                if first > second:
                    computed = 1
                else:
                    computed = 0
            else:
                computed = If(UGT(first, second), BitVecVal(1, 256), BitVecVal(0, 256))
            computed = simplify(computed) if is_expr(computed) else computed
            instruction_object.data_out = [computed]
            stack.insert(0, computed)
            # Check for signed conversion
            check_signedness_conversion(computed, type_information, False, False, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "SLT":  # Not fully faithful to signed comparison
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            instruction_object = InstructionObject(instr_parts[0], [], [])
            first = stack.pop(0)
            second = stack.pop(0)
            instruction_object.data_in = [first, second]
            if isAllReal(first, second):
                first = to_signed(first)
                second = to_signed(second)
                if first < second:
                    computed = 1
                else:
                    computed = 0
            else:
                computed = If(first < second, BitVecVal(1, 256), BitVecVal(0, 256))
            computed = simplify(computed) if is_expr(computed) else computed
            instruction_object.data_out = [computed]
            stack.insert(0, computed)
            # Check for signedness conversion
            check_signedness_conversion(computed, type_information, False, True, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "SGT":  # Not fully faithful to signed comparison
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            instruction_object = InstructionObject(instr_parts[0], [], [])
            first = stack.pop(0)
            second = stack.pop(0)
            instruction_object.data_in = [first, second]
            if isAllReal(first, second):
                first = to_signed(first)
                second = to_signed(second)
                if first > second:
                    computed = 1
                else:
                    computed = 0
            else:
                computed = If(first > second, BitVecVal(1, 256), BitVecVal(0, 256))
            computed = simplify(computed) if is_expr(computed) else computed
            instruction_object.data_out = [computed]
            stack.insert(0, computed)
            # Check for signedness conversion
            check_signedness_conversion(computed, type_information, False, True, instruction_object, path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "EQ":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            first = stack.pop(0)
            second = stack.pop(0)
            if isAllReal(first, second):
                if first == second:
                    computed = 1
                else:
                    computed = 0
            else:
                computed = If(first == second, BitVecVal(1, 256), BitVecVal(0, 256))
            computed = simplify(computed) if is_expr(computed) else computed
            stack.insert(0, computed)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "ISZERO":
        # Tricky: this instruction works on both boolean and integer,
        # when we have a symbolic expression, type error might occur
        # Currently handled by try and catch
        if len(stack) > 0:
            global_state["pc"] = global_state["pc"] + 1
            flag = stack.pop(0)
            if isReal(flag):
                if flag == 0:
                    computed = 1
                else:
                    computed = 0
            else:
                computed = If(flag == 0, BitVecVal(1, 256), BitVecVal(0, 256))
            computed = simplify(computed) if is_expr(computed) else computed
            stack.insert(0, computed)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "AND":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            instruction_object = InstructionObject(instr_parts[0], [], [])
            first = stack.pop(0)
            second = stack.pop(0)
            instruction_object.data_in = [first, second]
            computed = first & second
            computed = simplify(computed) if is_expr(computed) else computed
            instruction_object.data_out = [computed]
            stack.insert(0, computed)
            # Check for width conversion
            if is_input_tainted(instruction_object) or global_params.LOOP_LIMIT >= 256:
                conversion = check_width_conversion(first, second, computed, instruction_object, vertices[params.block], path_conditions_and_vars["path_condition"], arithmetic_errors, arithmetic_models, global_state["pc"] - 1)
                if conversion:
                    if not computed in width_conversions:
                        width_conversions.append(computed)#添加涉及宽度拓展的运算的结果去width_conversions中
                else:
                    if computed in width_conversions:#如果已经在width_conversions
                        for arithmetic_error in arithmetic_errors:
                            if computed == arithmetic_error["instruction"].data_out[0]:
                                arithmetic_errors.remove(arithmetic_error)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "OR":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            first = stack.pop(0)
            second = stack.pop(0)
            computed = first | second
            computed = simplify(computed) if is_expr(computed) else computed
            stack.insert(0, computed)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "XOR":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            first = stack.pop(0)
            second = stack.pop(0)
            computed = first ^ second
            computed = simplify(computed) if is_expr(computed) else computed
            stack.insert(0, computed)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "NOT":
        if len(stack) > 0:
            global_state["pc"] = global_state["pc"] + 1
            first = stack.pop(0)
            computed = (~first) & UNSIGNED_BOUND_NUMBER
            computed = simplify(computed) if is_expr(computed) else computed
            stack.insert(0, computed)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "BYTE":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            first = stack.pop(0)
            byte_index = 32 - first - 1
            second = stack.pop(0)

            if isAllReal(first, second):
                if first >= 32 or first < 0:
                    computed = 0
                else:
                    computed = second & (255 << (8 * byte_index))
                    computed = computed >> (8 * byte_index)
            else:
                first = to_symbolic(first)
                second = to_symbolic(second)
                solver.push()
                solver.add( Not (Or( first >= 32, first < 0 ) ) )
                if check_solver(solver) == unsat:
                    computed = 0
                else:
                    computed = second & (255 << (8 * byte_index))
                    computed = computed >> (8 * byte_index)
                solver.pop()
            computed = simplify(computed) if is_expr(computed) else computed
            stack.insert(0, computed)
        else:
            raise ValueError('STACK underflow')
    #
    # 20s: SHA3
    #
    elif instr_parts[0] == "SHA3":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            s0 = stack.pop(0)
            s1 = stack.pop(0)
            if isAllReal(s0, s1):
                data = [mem[s0+i*32] for i in range(s1/32)]
                input = ''
                symbolic = False
                for value in data:
                    if is_expr(value):
                        input += str(value)
                        symbolic = True
                    else:
                        input += binascii.unhexlify('%064x' % value)
                if input in sha3_list:
                    stack.insert(0, sha3_list[input])
                else:
                    if symbolic:
                        new_var_name = gen.gen_arbitrary_var()
                        new_var = BitVec(new_var_name, 256)
                        sha3_list[input] = new_var
                        path_conditions_and_vars[new_var_name] = new_var
                        stack.insert(0, new_var)
                    else:
                        hash = sha3.keccak_256(input).hexdigest()
                        new_var = int(hash, 16)
                        sha3_list[input] = new_var
                        stack.insert(0, new_var)
            else:
                new_var_name = gen.gen_arbitrary_var()
                new_var = BitVec(new_var_name, 256)
                path_conditions_and_vars[new_var_name] = new_var
                stack.insert(0, new_var)
        else:
            raise ValueError('STACK underflow')
    #
    # 30s: Environment Information
    #
    elif instr_parts[0] == "ADDRESS":  # get address of currently executing account
        global_state["pc"] = global_state["pc"] + 1
        stack.insert(0, path_conditions_and_vars["Ia"])
    elif instr_parts[0] == "BALANCE":
        if len(stack) > 0:
            global_state["pc"] = global_state["pc"] + 1
            address = stack.pop(0)
            if isReal(address) and global_params.USE_GLOBAL_BLOCKCHAIN:
                new_var = data_source.getBalance(address)
            else:
                new_var_name = gen.gen_balance_var()
                if new_var_name in path_conditions_and_vars:
                    new_var = path_conditions_and_vars[new_var_name]
                else:
                    new_var = BitVec(new_var_name, 256)
                    path_conditions_and_vars[new_var_name] = new_var
            if isReal(address):
                hashed_address = "concrete_address_" + str(address)
            else:
                hashed_address = str(address)
            global_state["balance"][hashed_address] = new_var
            stack.insert(0, new_var)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "CALLER":  # get caller address caller调用函数并执行
        # that is directly responsible for this execution#直接负责此执行
        global_state["pc"] = global_state["pc"] + 1
        stack.insert(0, global_state["sender_address"])
    elif instr_parts[0] == "ORIGIN":  # get execution origination address
        global_state["pc"] = global_state["pc"] + 1
        stack.insert(0, global_state["origin"])
    elif instr_parts[0] == "CALLVALUE":  # get value of this transaction
        global_state["pc"] = global_state["pc"] + 1
        stack.insert(0, global_state["value"])
    elif instr_parts[0] == "CALLDATALOAD":  # from input data from environment
        if len(stack) > 0:
            global_state["pc"] = global_state["pc"] + 1
            position = stack.pop(0)
            if source_map:
                source_code = source_map.find_source_code(global_state["pc"] - 1)
                if source_code.startswith("function") and isReal(position):
                    idx1 = source_code.index("(") + 1
                    idx2 = source_code.index(")")
                    params_code = source_code[idx1:idx2]
                    params_list = params_code.split(",")
                    params_list = [param.split(" ")[-1] for param in params_list]
                    param_idx = (position - 4) / 32
                    new_var_name = params_list[param_idx]
                    source_map.var_names.append(new_var_name)
                else:
                    new_var_name = gen.gen_data_var(position)
            else:
                new_var_name = gen.gen_data_var(position)
            if new_var_name in path_conditions_and_vars:
                new_var = path_conditions_and_vars[new_var_name]
            else:
                new_var = BitVec(new_var_name, 256)
                path_conditions_and_vars[new_var_name] = new_var
            stack.insert(0, new_var)
            initialize_var(new_var, type_information)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "CALLDATASIZE":
        global_state["pc"] = global_state["pc"] + 1
        new_var_name = gen.gen_data_size()
        if new_var_name in path_conditions_and_vars:
            new_var = path_conditions_and_vars[new_var_name]
        else:
            new_var = BitVec(new_var_name, 256)
            path_conditions_and_vars[new_var_name] = new_var
        stack.insert(0, new_var)
        initialize_var(new_var, type_information)
    elif instr_parts[0] == "CALLDATACOPY":  # Copy input data to memory模拟内存
        #  TODO: Don't know how to simulate this yet
        if len(stack) > 2:
            global_state["pc"] = global_state["pc"] + 1
            stack.pop(0)
            stack.pop(0)
            stack.pop(0)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "CODESIZE":
        if c_name.endswith('.disasm'):
            evm_file_name = c_name[:-7]
        else:
            evm_file_name = c_name
        with open(evm_file_name, 'r') as evm_file:
            evm = evm_file.read()[:-1]
            code_size = len(evm)/2
            stack.insert(0, code_size)
    elif instr_parts[0] == "CODECOPY":
        if len(stack) > 2:
            global_state["pc"] = global_state["pc"] + 1
            mem_location = stack.pop(0)
            code_from = stack.pop(0)
            no_bytes = stack.pop(0)
            current_miu_i = global_state["miu_i"]

            if isAllReal(mem_location, current_miu_i, code_from, no_bytes):
                temp = long(math.ceil((mem_location + no_bytes) / float(32)))
                if temp > current_miu_i:
                    current_miu_i = temp

                if c_name.endswith('.disasm'):
                    evm_file_name = c_name[:-7]
                else:
                    evm_file_name = c_name
                with open(evm_file_name, 'r') as evm_file:
                    evm = evm_file.read()[:-1]
                    start = code_from * 2
                    end = start + no_bytes * 2
                    code = evm[start: end]
                mem[mem_location] = int(code, 16)
            else:
                new_var_name = gen.gen_code_var("Ia", code_from, no_bytes)
                if new_var_name in path_conditions_and_vars:
                    new_var = path_conditions_and_vars[new_var_name]
                else:
                    new_var = BitVec(new_var_name, 256)
                    path_conditions_and_vars[new_var_name] = new_var

                temp = ((mem_location + no_bytes) / 32) + 1
                current_miu_i = to_symbolic(current_miu_i)
                expression = current_miu_i < temp
                solver.push()
                solver.add(expression)
                if check_solver(solver) != unsat:
                    current_miu_i = If(expression, temp, current_miu_i)
                solver.pop()
                mem.clear() # very conservative
                mem[str(mem_location)] = new_var
            global_state["miu_i"] = current_miu_i
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "GASPRICE":
        global_state["pc"] = global_state["pc"] + 1
        stack.insert(0, global_state["gas_price"])
    elif instr_parts[0] == "EXTCODESIZE":
        if len(stack) > 0:
            global_state["pc"] = global_state["pc"] + 1
            address = stack.pop(0)
            if isReal(address) and global_params.USE_GLOBAL_BLOCKCHAIN:
                code = data_source.getCode(address)
                stack.insert(0, len(code)/2)
            else:
                #not handled yet
                new_var_name = gen.gen_code_size_var(address)
                if new_var_name in path_conditions_and_vars:
                    new_var = path_conditions_and_vars[new_var_name]
                else:
                    new_var = BitVec(new_var_name, 256)
                    path_conditions_and_vars[new_var_name] = new_var
                stack.insert(0, new_var)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "EXTCODECOPY":
        if len(stack) > 3:
            global_state["pc"] = global_state["pc"] + 1
            address = stack.pop(0)
            mem_location = stack.pop(0)
            code_from = stack.pop(0)
            no_bytes = stack.pop(0)
            current_miu_i = global_state["miu_i"]

            if isAllReal(address, mem_location, current_miu_i, code_from, no_bytes) and USE_GLOBAL_BLOCKCHAIN:
                temp = long(math.ceil((mem_location + no_bytes) / float(32)))
                if temp > current_miu_i:
                    current_miu_i = temp

                evm = data_source.getCode(address)
                start = code_from * 2
                end = start + no_bytes * 2
                code = evm[start: end]
                mem[mem_location] = int(code, 16)
            else:
                new_var_name = gen.gen_code_var(address, code_from, no_bytes)
                if new_var_name in path_conditions_and_vars:
                    new_var = path_conditions_and_vars[new_var_name]
                else:
                    new_var = BitVec(new_var_name, 256)
                    path_conditions_and_vars[new_var_name] = new_var

                temp = ((mem_location + no_bytes) / 32) + 1
                current_miu_i = to_symbolic(current_miu_i)
                expression = current_miu_i < temp
                solver.push()
                solver.add(expression)
                if check_solver(solver) != unsat:
                    current_miu_i = If(expression, temp, current_miu_i)
                solver.pop()
                mem.clear() # very conservative
                mem[str(mem_location)] = new_var
            global_state["miu_i"] = current_miu_i
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "RETURNDATACOPY":
        if len(stack) > 2:
            global_state["pc"] += 1
            stack.pop(0)
            stack.pop(0)
            stack.pop(0)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "RETURNDATASIZE":
        global_state["pc"] += 1
        new_var_name = gen.gen_arbitrary_var()
        new_var = BitVec(new_var_name, 256)
        stack.insert(0, new_var)
    #
    #  40s: Block Information
    #
    elif instr_parts[0] == "BLOCKHASH":  # information from block header
        if len(stack) > 0:
            global_state["pc"] = global_state["pc"] + 1
            stack.pop(0)
            new_var_name = "IH_blockhash"
            if new_var_name in path_conditions_and_vars:
                new_var = path_conditions_and_vars[new_var_name]
            else:
                new_var = BitVec(new_var_name, 256)
                path_conditions_and_vars[new_var_name] = new_var
            stack.insert(0, new_var)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "COINBASE":  # information from block header
        global_state["pc"] = global_state["pc"] + 1
        stack.insert(0, global_state["currentCoinbase"])
    elif instr_parts[0] == "TIMESTAMP":  # information from block header
        global_state["pc"] = global_state["pc"] + 1
        stack.insert(0, global_state["currentTimestamp"])
    elif instr_parts[0] == "NUMBER":  # information from block header
        global_state["pc"] = global_state["pc"] + 1
        stack.insert(0, global_state["currentNumber"])
    elif instr_parts[0] == "DIFFICULTY":  # information from block header
        global_state["pc"] = global_state["pc"] + 1
        stack.insert(0, global_state["currentDifficulty"])
    elif instr_parts[0] == "GASLIMIT":  # information from block header
        global_state["pc"] = global_state["pc"] + 1
        stack.insert(0, global_state["currentGasLimit"])
    #
    #  50s: Stack, Memory, Storage, and Flow Information
    #
    elif instr_parts[0] == "POP":
        if len(stack) > 0:
            global_state["pc"] = global_state["pc"] + 1
            stack.pop(0)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "MLOAD":
        if len(stack) > 0:
            global_state["pc"] = global_state["pc"] + 1
            address = stack.pop(0)
            current_miu_i = global_state["miu_i"]
            if isAllReal(address, current_miu_i) and address in mem:
                temp = long(math.ceil((address + 32) / float(32)))
                if temp > current_miu_i:
                    current_miu_i = temp
                value = mem[address]
                stack.insert(0, value)
                log.debug("temp: " + str(temp))
                log.debug("current_miu_i: " + str(current_miu_i))
            else:
                temp = ((address + 31) / 32) + 1
                current_miu_i = to_symbolic(current_miu_i)
                expression = current_miu_i < temp
                solver.push()
                solver.add(expression)
                if check_solver(solver) != unsat:
                    # this means that it is possibly that current_miu_i < temp
                    current_miu_i = If(expression,temp,current_miu_i)
                solver.pop()
                if address in mem:
                    value = mem[address]
                    stack.insert(0, value)
                else:
                    new_var_name = gen.gen_mem_var(address)
                    if not new_var_name in path_conditions_and_vars:
                        path_conditions_and_vars[new_var_name] = BitVec(new_var_name, 256)
                    new_var = path_conditions_and_vars[new_var_name]
                    stack.insert(0, new_var)
                    mem[address] = new_var
                    initialize_var(new_var, type_information)
                log.debug("temp: " + str(temp))
                log.debug("current_miu_i: " + str(current_miu_i))
            global_state["miu_i"] = current_miu_i
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "MSTORE":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            stored_address = stack.pop(0)
            stored_value = stack.pop(0)
            current_miu_i = global_state["miu_i"]
            if isReal(stored_address):
                # preparing data for hashing later
                old_size = len(memory) // 32
                new_size = ceil32(stored_address + 32) // 32
                mem_extend = (new_size - old_size) * 32
                memory.extend([0] * mem_extend)
                value = stored_value
                for i in range(31, -1, -1):
                    memory[stored_address + i] = value % 256
                    value /= 256
            if isAllReal(stored_address, current_miu_i):
                temp = long(math.ceil((stored_address + 32) / float(32)))
                if temp > current_miu_i:
                    current_miu_i = temp
                mem[stored_address] = stored_value  # note that the stored_value could be symbolic
                log.debug("temp: " + str(temp))
                log.debug("current_miu_i: " + str(current_miu_i))
            else:
                log.debug("temp: " + str(stored_address))
                temp = ((stored_address + 31) / 32) + 1
                log.debug("current_miu_i: " + str(current_miu_i))
                expression = current_miu_i < temp
                log.debug("Expression: " + str(expression))
                solver.push()
                solver.add(expression)
                if check_solver(solver) != unsat:
                    # this means that it is possibly that current_miu_i < temp
                    current_miu_i = If(expression,temp,current_miu_i)
                solver.pop()
                #mem.clear()  # very conservative
                mem[stored_address] = stored_value
                log.debug("temp: " + str(temp))
                log.debug("current_miu_i: " + str(current_miu_i))
            global_state["miu_i"] = current_miu_i
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "MSTORE8":
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            stored_address = stack.pop(0)
            temp_value = stack.pop(0)
            stored_value = temp_value % 256  # get the least byte
            current_miu_i = global_state["miu_i"]
            if isAllReal(stored_address, current_miu_i):
                temp = long(math.ceil((stored_address + 1) / float(32)))
                if temp > current_miu_i:
                    current_miu_i = temp
                mem[stored_address] = stored_value  # note that the stored_value could be symbolic
            else:
                temp = (stored_address / 32) + 1
                if isReal(current_miu_i):
                    current_miu_i = BitVecVal(current_miu_i, 256)
                expression = current_miu_i < temp
                solver.push()
                solver.add(expression)
                if check_solver(solver) != unsat:
                    # this means that it is possibly that current_miu_i < temp
                    current_miu_i = If(expression,temp,current_miu_i)
                solver.pop()
                mem[stored_address] = stored_value
                #mem.clear()  # very conservative
            global_state["miu_i"] = current_miu_i
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "SLOAD":
        if len(stack) > 0:
            global_state["pc"] = global_state["pc"] + 1
            address = stack.pop(0)
            if is_expr(address):
                address = simplify(address)
            if address in global_state["Ia"]:
                value = global_state["Ia"][address]
                stack.insert(0, value)
            else:
                #new_var_name = None
                #if source_map:
                #    new_var_name = source_map.find_source_code(global_state["pc"] - 1)
                #    operators = '[-+*/%|&^!><=]'
                #    new_var_name = re.compile(operators).split(new_var_name)[0].strip()
                #    if source_map.is_a_parameter_or_state_variable(new_var_name):
                #        new_var_name = "Ia_store" + "-" + str(address) + "-" + new_var_name
                #    else:
                #        new_var_name = gen.gen_owner_store_var(address)
                #else:
                new_var_name = gen.gen_owner_store_var(address)
                if not new_var_name in path_conditions_and_vars:
                    if address.__class__.__name__ == "BitVecNumRef":
                        address = address.as_long()
                    else:
                        path_conditions_and_vars[new_var_name] = BitVec(new_var_name, 256)
                new_var = path_conditions_and_vars[new_var_name]
                stack.insert(0, new_var)
                global_state["Ia"][address] = new_var
        else:
            raise ValueError('STACK underflow')

    elif instr_parts[0] == "SSTORE":
        if len(stack) > 1:
            for call_pc in calls:
                validator.instructions_vulnerable_to_callstack[call_pc] = True
            global_state["pc"] = global_state["pc"] + 1
            stored_address = stack.pop(0)
            stored_value = stack.pop(0)
            global_state["Ia"][stored_address] = stored_value
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "JUMP":
        if len(stack) > 0:
            target_address = stack.pop(0)
            if isSymbolic(target_address):
                try:
                    target_address = int(str(simplify(target_address)))
                except:
                    raise TypeError("Target address must be an integer")
            #if vertices[start].get_jump_target() != target_address:
            vertices[start].set_jump_target(target_address)
            if target_address not in edges[start]:
                edges[start].append(target_address)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "JUMPI":
        # We need to prepare two branches
        if len(stack) > 1:
            target_address = stack.pop(0)
            if isSymbolic(target_address):
                try:
                    target_address = int(str(simplify(target_address)))
                except:
                    raise TypeError("Target address must be an integer")
            vertices[start].set_jump_target(target_address)
            flag = stack.pop(0)

            if flag.__class__.__name__ == "BitVecNumRef":
                flag = flag.as_long()

            #branch_expression = (BitVecVal(0, 1) == BitVecVal(1, 1))
            #if isReal(flag):
            #    if flag != 0:
            #        branch_expression = True

            #if isReal(flag):
            #    if flag != 0:
            #        branch_expression = (BitVecVal(1, 1) != BitVecVal(0, 1))
            #    else:
            #        branch_expression = (BitVecVal(0, 1) == BitVecVal(0, 1))

            branch_expression = (flag != 0)

            """if isReal(flag) or flag.__class__.__name__ == "BitVecNumRef":
                new_var_name = gen.gen_conditional_var()
                new_var = BitVec(new_var_name, 256)
                if flag.__class__.__name__ == "BitVecNumRef":
                    flag = flag.as_long()
                if flag != 0:
                    branch_expression = (If(new_var == 1, 1, 0) != 0)
                else:
                    branch_expression = (new_var == 0)
            else:
                branch_expression = (flag != 0)"""

            vertices[start].set_branch_expression(branch_expression)
            if target_address not in edges[start]:
                edges[start].append(target_address)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "PC":
        stack.insert(0, global_state["pc"])
        global_state["pc"] = global_state["pc"] + 1
    elif instr_parts[0] == "MSIZE":
        global_state["pc"] = global_state["pc"] + 1
        msize = 32 * global_state["miu_i"]
        stack.insert(0, msize)
    elif instr_parts[0] == "GAS":
        # In general, we do not have this precisely. It depends on both
        # the initial gas and the amount has been depleted
        # we need to think about this in the future, in case precise gas
        # can be tracked
        global_state["pc"] = global_state["pc"] + 1
        new_var_name = gen.gen_gas_var()
        new_var = BitVec(new_var_name, 256)
        path_conditions_and_vars[new_var_name] = new_var
        stack.insert(0, new_var)
    elif instr_parts[0] == "JUMPDEST":
        # Literally do nothing
        global_state["pc"] = global_state["pc"] + 1
    #
    #  60s & 70s: Push Operations
    #
    elif instr_parts[0].startswith('PUSH', 0):  # this is a push instruction
        position = int(instr_parts[0][4:], 10)#instr_parts[0][4:]是1，按照十进制转换为int
        global_state["pc"] = global_state["pc"] + 1 + position#global_state["pc"]：2
        pushed_value = int(instr_parts[1], 16)#0x60，按照十六进制转为int，96
        stack.insert(0, pushed_value)
        if global_params.UNIT_TEST == 3: # test evm symbolic
            stack[0] = BitVecVal(stack[0], 256)
    #
    #  80s: Duplication Operations
    #
    elif instr_parts[0].startswith("DUP", 0):
        global_state["pc"] = global_state["pc"] + 1
        position = int(instr_parts[0][3:], 10) - 1
        if len(stack) > position:
            duplicate = stack[position]
            stack.insert(0, duplicate)
        else:
            raise ValueError('STACK underflow')

    #
    #  90s: Swap Operations
    #
    elif instr_parts[0].startswith("SWAP", 0):
        global_state["pc"] = global_state["pc"] + 1
        position = int(instr_parts[0][4:], 10)
        if len(stack) > position:
            temp = stack[position]
            stack[position] = stack[0]
            stack[0] = temp
        else:
            raise ValueError('STACK underflow')

    #
    #  a0s: Logging Operations
    #
    elif instr_parts[0] in ("LOG0", "LOG1", "LOG2", "LOG3", "LOG4"):
        global_state["pc"] = global_state["pc"] + 1
        # We do not simulate these log operations
        num_of_pops = 2 + int(instr_parts[0][3:])
        while num_of_pops > 0:
            stack.pop(0)
            num_of_pops -= 1

    #
    #  f0s: System Operations
    #
    elif instr_parts[0] == "CREATE":
        if len(stack) > 2:
            global_state["pc"] += 1
            stack.pop(0)
            stack.pop(0)
            stack.pop(0)
            new_var_name = gen.gen_arbitrary_var()
            new_var = BitVec(new_var_name, 256)
            stack.insert(0, new_var)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "CALL":
        # TODO: Need to handle miu_i
        if len(stack) > 6:
            calls.append(global_state["pc"])
            for call_pc in calls:
                if call_pc not in validator.instructions_vulnerable_to_callstack:
                    validator.instructions_vulnerable_to_callstack[call_pc] = False
            global_state["pc"] = global_state["pc"] + 1
            outgas = stack.pop(0)
            recipient = stack.pop(0)
            transfer_amount = stack.pop(0)
            start_data_input = stack.pop(0)
            size_data_input = stack.pop(0)
            start_data_output = stack.pop(0)
            size_data_ouput = stack.pop(0)
            # in the paper, it is shaky when the size of data output is
            # min of stack[6] and the | o |

            if isReal(transfer_amount) and transfer_amount == 0:
                stack.insert(0, 1)   # x = 0
            else:
                # Let us ignore the call depth
                balance_ia = global_state["balance"]["Ia"]
                is_enough_fund = (transfer_amount <= balance_ia)
                solver.push()
                solver.add(is_enough_fund)
                if check_solver(solver) == unsat:
                    # this means not enough fund, thus the execution will result in exception
                    solver.pop()
                    stack.insert(0, 0)   # x = 0
                else:
                    # the execution is possibly okay
                    stack.insert(0, 1)   # x = 1
                    solver.pop()
                    solver.add(is_enough_fund)
                    path_conditions_and_vars["path_condition"].append(is_enough_fund)
                    last_idx = len(path_conditions_and_vars["path_condition"]) - 1
                    analysis["time_dependency_bug"][last_idx] = global_state["pc"] - 1
                    new_balance_ia = (balance_ia - transfer_amount)
                    global_state["balance"]["Ia"] = new_balance_ia
                    address_is = path_conditions_and_vars["Is"]
                    address_is = (address_is & CONSTANT_ONES_159)
                    boolean_expression = (recipient != address_is)
                    solver.push()
                    solver.add(boolean_expression)
                    if check_solver(solver) == unsat:
                        solver.pop()
                        new_balance_is = (global_state["balance"]["Is"] + transfer_amount)
                        global_state["balance"]["Is"] = new_balance_is
                    else:
                        solver.pop()
                        if isReal(recipient):
                            new_address_name = "concrete_address_" + str(recipient)
                        else:
                            new_address_name = gen.gen_arbitrary_address_var()
                        old_balance_name = gen.gen_arbitrary_var()
                        old_balance = BitVec(old_balance_name, 256)
                        path_conditions_and_vars[old_balance_name] = old_balance
                        constraint = (old_balance >= 0)
                        solver.add(constraint)
                        path_conditions_and_vars["path_condition"].append(constraint)
                        new_balance = (old_balance + transfer_amount)
                        global_state["balance"][new_address_name] = new_balance
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "CALLCODE":
        # TODO: Need to handle miu_i
        if len(stack) > 6:
            calls.append(global_state["pc"])
            for call_pc in calls:
                if call_pc not in validator.instructions_vulnerable_to_callstack:
                    validator.instructions_vulnerable_to_callstack[call_pc] = False
            global_state["pc"] = global_state["pc"] + 1
            outgas = stack.pop(0)
            stack.pop(0) # this is not used as recipient
            transfer_amount = stack.pop(0)
            start_data_input = stack.pop(0)
            size_data_input = stack.pop(0)
            start_data_output = stack.pop(0)
            size_data_ouput = stack.pop(0)
            # in the paper, it is shaky when the size of data output is
            # min of stack[6] and the | o |

            if isReal(transfer_amount):
                if transfer_amount == 0:
                    stack.insert(0, 1)   # x = 0
                    return

            # Let us ignore the call depth
            balance_ia = global_state["balance"]["Ia"]
            is_enough_fund = (transfer_amount <= balance_ia)
            solver.push()
            solver.add(is_enough_fund)
            if check_solver(solver) == unsat:
                # this means not enough fund, thus the execution will result in exception
                solver.pop()
                stack.insert(0, 0)   # x = 0
            else:
                # the execution is possibly okay
                stack.insert(0, 1)   # x = 1
                solver.pop()
                solver.add(is_enough_fund)
                path_conditions_and_vars["path_condition"].append(is_enough_fund)
                last_idx = len(path_conditions_and_vars["path_condition"]) - 1
                analysis["time_dependency_bug"][last_idx]
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "DELEGATECALL":
        if len(stack) > 5:
            global_state["pc"] += 1
            stack.pop(0)
            stack.pop(0)
            stack.pop(0)
            stack.pop(0)
            stack.pop(0)
            stack.pop(0)
            new_var_name = gen.gen_arbitrary_var()
            new_var = BitVec(new_var_name, 256)
            stack.insert(0, new_var)
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "RETURN" or instr_parts[0] == "REVERT":
        # TODO: Need to handle miu_i
        if len(stack) > 1:
            global_state["pc"] = global_state["pc"] + 1
            stack.pop(0)
            stack.pop(0)
            pass
        else:
            raise ValueError('STACK underflow')
    elif instr_parts[0] == "SUICIDE":
        global_state["pc"] = global_state["pc"] + 1
        recipient = stack.pop(0)
        transfer_amount = global_state["balance"]["Ia"]
        global_state["balance"]["Ia"] = 0
        if isReal(recipient):
            new_address_name = "concrete_address_" + str(recipient)
        else:
            new_address_name = gen.gen_arbitrary_address_var()
        old_balance_name = gen.gen_arbitrary_var()
        old_balance = BitVec(old_balance_name, 256)
        path_conditions_and_vars[old_balance_name] = old_balance
        constraint = (old_balance >= 0)
        solver.add(constraint)
        path_conditions_and_vars["path_condition"].append(constraint)
        new_balance = (old_balance + transfer_amount)
        global_state["balance"][new_address_name] = new_balance
        # TODO
        #return
    elif instr_parts[0] == "INVALID":
        pass
    elif instr_parts[0] == "ASSERTFAIL":
        try:
            if source_map:
                source_code = source_map.find_source_code(global_state["pc"])
                if "assert" in source_code:
                    global_problematic_pcs["assertion_failure"].append(Assertion(global_state["pc"], models[-1]))
                elif func_call != -1:
                    global_problematic_pcs["assertion_failure"].append(Assertion(func_call, models[-1]))
            else:
                global_problematic_pcs["assertion_failure"].append(Assertion(global_state["pc"], models[-1]))
        except:
            pass
    else:
        print("UNKNOWN INSTRUCTION: " + instr_parts[0])
        if global_params.UNIT_TEST == 2 or global_params.UNIT_TEST == 3:
            log.critical("Unkown instruction: %s" % instr_parts[0])
            exit(UNKOWN_INSTRUCTION)
        raise Exception('UNKNOWN INSTRUCTION: ' + instr_parts[0])

    """ Perform taint analysis """
    try:
        next_blocks = []#start=0
        if start in edges:#{0: [13], 13: [65], 465: [], 539: [546], 546: [], 550: [], 424: [], 428: [437], 558: [], 437: [], 441: [], 320: [], 65: [76], 324: [333], 454: [461], 584: [], 76: [320], 333: [], 461: [], 337: [424], 473: [], 743: [], 621: []}各个块的边
            for edge in edges[start]:#edge：13，edges[start]：[13]
                if edge in vertices:#vertices：{0: <basicblock.BasicBlock instance at 0x7fc7ef157518>, 13: <basicblock.BasicBlock instance at 0x7fc7efa33638>, 465: <basicblock.BasicBlock instance at 0x7fc7ef14a200>, 539: <basicblock.BasicBlock instance at 0x7fc7ef1de878>, 546: <basicblock.BasicBlock instance at 0x7fc7ef1de638>, 550: <basicblock.BasicBlock instance at 0x7fc7ef1de4d0>, 424: <basicblock.BasicBlock instance at 0x7fc7ef1deef0>, 428: <basicblock.BasicBlock instance at 0x7fc7ef1dea28>, 558: <basicblock.BasicBlock instance at 0x7fc7ef1ded88>, 437: <basicblock.BasicBlock instance at 0x7fc7ef1de950>, 441: <basicblock.BasicBlock instance at 0x7fc7ef1decf8>, 320: <basicblock.BasicBlock instance at 0x7fc7ef1de680>, 65: <basicblock.BasicBlock instance at 0x7fc7ef1de908>, 324: <basicblock.BasicBlock instance at 0x7fc7ef1de560>, 454: <basicblock.BasicBlock instance at 0x7fc7ef1de128>, 584: <basicblock.BasicBlock instance at 0x7fc7ef1de3f8>, 76: <basicblock.BasicBlock instance at 0x7fc7ef1de7a0>, 333: <basicblock.BasicBlock instance at 0x7fc7ef1de248>, 461: <basicblock.BasicBlock instance at 0x7fc7ef1def38>, 337: <basicblock.BasicBlock instance at 0x7fc7ef14a320>, 473: <basicblock.BasicBlock instance at 0x7fc7ef14a830>, 743: <basicblock.BasicBlock instance at 0x7fc7ef14a6c8>, 621: <basicblock.BasicBlock instance at 0x7fc7ef14acb0>}
                    next_blocks.append(vertices[edge])#vertices[edge]：<basicblock.BasicBlock instance at 0x7fc7efa33638>
        perform_taint_analysis(vertices[params.pre_block], vertices[params.block], next_blocks, previous_pc, instr_parts[0], previous_stack, stack, arithmetic_errors)
    except Exception as e:
#previous_block = <basicblock.BasicBlock instance at 0x7fc7ef157518>即vertices[params.pre_block]
# current_block = <basicblock.BasicBlock instance at 0x7fc7ef157518>即vertices[params.block]
# next_blocks = [<basicblock.BasicBlock instance at 0x7fc7efa33638>]即next_blocks
        traceback.print_exc()
        print "Exception in taint analysis: "+str(e)
        raise e

    try:
        print_state(stack, mem, global_state)
    except:
        log.debug("Error: Debugging states")

# Detect if a money flow depends on the timestamp
def detect_time_dependency():
    global results
    global source_map
    global validator

    TIMESTAMP_VAR = "IH_s"
    is_dependant = False
    pcs = []
    if global_params.PRINT_PATHS:
        log.info("ALL PATH CONDITIONS")
    for i, cond in enumerate(path_conditions):
        if global_params.PRINT_PATHS:
            log.info("PATH " + str(i + 1) + ": " + str(cond))
        for j, expr in enumerate(cond):
            if is_expr(expr):
                if TIMESTAMP_VAR in str(expr) and j in global_problematic_pcs["time_dependency_bug"][i]:
                    pcs.append(global_problematic_pcs["time_dependency_bug"][i][j])
                    is_dependant = True
                    continue

    if source_map:
        pcs = validator.remove_false_positives(pcs)
        s = source_map.to_str(pcs, "Time dependency bug")
        if s:
            results["time_dependency"] = s
        s = "\t  Time dependency bug: \t True" + s if s else "\t  Time dependency bug: \t False"
        log.info(s)
    else:
        results["time_dependency"] = bool(pcs)
        log.info("\t  Timedependency bug: \t %s", bool(pcs))

    if global_params.REPORT_MODE:
        file_name = c_name.split("/")[len(c_name.split("/"))-1].split(".")[0]
        report_file = file_name + '.report'
        with open(report_file, 'w') as rfile:
            if is_dependant:
                rfile.write("yes\n")
            else:
                rfile.write("no\n")


# detect if two paths send money to different people
def detect_money_concurrency():
    global results
    global source_map
    global validator

    n = len(money_flow_all_paths)
    for i in range(n):
        log.debug("Path " + str(i) + ": " + str(money_flow_all_paths[i]))
        log.debug(all_gs[i])
    i = 0
    false_positive = []
    concurrency_paths = []
    flows = []
    for flow in money_flow_all_paths:
        i += 1
        if len(flow) == 1:
            continue  # pass all flows which do not do anything with money
        for j in range(i, n):
            jflow = money_flow_all_paths[j]
            if len(jflow) == 1:
                continue
            if is_diff(flow, jflow):
                flows.append(global_problematic_pcs["money_concurrency_bug"][i-1])
                flows.append(global_problematic_pcs["money_concurrency_bug"][j])
                concurrency_paths.append([i-1, j])
                if global_params.CHECK_CONCURRENCY_FP and \
                        is_false_positive(i-1, j, all_gs, path_conditions) and \
                        is_false_positive(j, i-1, all_gs, path_conditions):
                    false_positive.append([i-1, j])
                break
        if flows:
            break

    if source_map:
        s = ""
        for idx, pcs in enumerate(flows):
            pcs = validator.remove_false_positives(pcs)
            if global_params.WEB:
                s += "Flow " + str(idx + 1) + ":<br />"
            else:
                s += "\nFlow " + str(idx + 1) + ":"
            for pc in pcs:
                source_code = source_map.find_source_code(pc).split("\n", 1)[0]
                if not source_code:
                    continue
                location = source_map.get_location(pc)
                if global_params.WEB:
                    s += "%s:%s:%s:<br />" % (source_map.cname.split(":", 1)[1], location['begin']['line'] + 1, location['begin']['column'] + 1)
                    s += "<span style='margin-left: 20px'>%s</span><br />" % source_code
                    s += "<span style='margin-left: 20px'>^</span><br />"
                else:
                    s += "\n%s:%s:%s\n" % (source_map.cname, location['begin']['line'] + 1, location['begin']['column'] + 1)
                    s += source_code + "\n"
                    s += "^"
        if s:
            if global_params.WEB:
                s = "Concurrency bug:<br />" + "<div style='margin-left: 20px'>" + s + "</div>"
            results["money_concurrency"] = s
        s = "\t  Concurrency bug: \t True" + s if s else "\t  Concurrency bug: \t False"
        log.info(s)
    else:
        results["money_concurrency"] = bool(flows)
        log.info("\t  Concurrency bug: \t %s", bool(flows))

    # if PRINT_MODE: print "All false positive cases: ", false_positive
    log.debug("Concurrency in paths: ")
    if global_params.REPORT_MODE:
        rfile.write("Number of path: " + str(n) + "\n")
        # number of FP detected
        rfile.write(str(len(false_positive)) + "\n")
        rfile.write(str(false_positive) + "\n")
        # number of total races
        rfile.write(str(len(concurrency_paths)) + "\n")
        # all the races
        rfile.write(str(concurrency_paths) + "\n")


# Detect if there is data concurrency in two different flows.
# e.g. if a flow modifies a value stored in the storage address and
# the other one reads that value in its execution
#检测两个不同的流中是否存在数据并发。
#例如，如果一个流修改了存储在存储地址中的值
#另一个在执行中读取该值
def detect_data_concurrency():
    sload_flows = data_flow_all_paths[0]
    sstore_flows = data_flow_all_paths[1]
    concurrency_addr = []
    for sflow in sstore_flows:
        for addr in sflow:
            for lflow in sload_flows:
                if addr in lflow:
                    if not addr in concurrency_addr:
                        concurrency_addr.append(addr)
                    break
    log.debug("data concurrency in storage " + str(concurrency_addr))

# Detect if any change in a storage address will result in a different
# flow of money. Currently I implement this detection by
# considering if a path condition contains
# a variable which is a storage address.
def detect_data_money_concurrency():
    n = len(money_flow_all_paths)
    sstore_flows = data_flow_all_paths[1]
    concurrency_addr = []
    for i in range(n):
        cond = path_conditions[i]
        list_vars = []
        for expr in cond:
            if is_expr(expr):
                list_vars += get_vars(expr)
        set_vars = set(i.decl().name() for i in list_vars)
        for sflow in sstore_flows:
            for addr in sflow:
                var_name = gen.gen_owner_store_var(addr)
                if var_name in set_vars:
                    concurrency_addr.append(var_name)
    log.debug("Concurrency in data that affects money flow: " + str(set(concurrency_addr)))



def check_callstack_attack(disasm):
    problematic_instructions = ['CALL', 'CALLCODE']
    pcs = []
    try:
        for i in xrange(0, len(disasm)):
            instruction = disasm[i]
            if instruction[1] in problematic_instructions:
                pc = int(instruction[0])
                if not disasm[i+1][1] == 'SWAP':
                    continue
                swap_num = int(disasm[i+1][2])
                for j in range(swap_num):
                    if not disasm[i+j+2][1] == 'POP':
                        continue
                opcode1 = disasm[i + swap_num + 2][1]
                opcode2 = disasm[i + swap_num + 3][1]
                opcode3 = disasm[i + swap_num + 4][1]
                if opcode1 == "ISZERO" \
                    or opcode1 == "DUP" and opcode2 == "ISZERO" \
                    or opcode1 == "JUMPDEST" and opcode2 == "ISZERO" \
                    or opcode1 == "JUMPDEST" and opcode2 == "DUP" and opcode3 == "ISZERO":
                        pass
                else:
                    pcs.append(pc)
    except:
        pass
    return pcs

def detect_callstack_attack():
    global results
    global source_map
    global validator

    disasm_data = open(c_name).read()
    instr_pattern = r"([\d]+) ([A-Z]+)([\d]+)?(?: => 0x)?(\S+)?"
    instr = re.findall(instr_pattern, disasm_data)
    pcs = check_callstack_attack(instr)
    pcs = validator.remove_callstack_false_positives(pcs)

    if source_map:
        pcs = validator.remove_false_positives(pcs)
        s = source_map.to_str(pcs, "Callstack bug")
        if s:
            results["callstack"] = s
        s = "\t  Callstack bug: \t True" + s if s else "\t  Callstack bug: \t False"
        log.info(s)
    else:
        results["callstack"] = bool(pcs)
        log.info("\t  Callstack bug: \t %s", bool(pcs))

def detect_reentrancy():
    global source_map
    global validator
    global results

    reentrancy_bug_found = any([v for sublist in reentrancy_all_paths for v in sublist])
    if source_map:
        pcs = global_problematic_pcs["reentrancy_bug"]
        pcs = validator.remove_false_positives(pcs)
        s = source_map.to_str(pcs, "Reentrancy bug")
        if s:
            results["reentrancy"] = s
        s = "\t  Reentrancy bug: \t True" + s if s else "\t  Reentrancy bug: \t False"
        log.info(s)
    else:
        results["reentrancy"] = reentrancy_bug_found
        log.info("\t  Reentrancy bug: \t %s", reentrancy_bug_found)

def detect_assertion_failure():
    global source_map
    global results

    assertions = global_problematic_pcs["assertion_failure"]
    d = {}
    for asrt in assertions:#asrt：assertion
        pos = str(source_map.instr_positions[asrt.pc])#instr_positions：instruction positions
        if pos not in d:
            d[pos] = asrt
    assertions = d.values()

    s = ""
    for asrt in assertions:
        location = source_map.get_location(asrt.pc)
        source_code = source_map.find_source_code(asrt.pc).split("\n", 1)[0]
        if global_params.WEB:
            s += "%s:%s:%s: Assertion failure:<br />" % (source_map.cname.split(":", 1)[1], location['begin']['line'] + 1, location['begin']['column'] + 1)
            s += "<span style='margin-left: 20px'>%s</span><br />" % source_code
            s += "<span style='margin-left: 20px'>^</span><br />"
            for variable in asrt.model.decls():
                var_name = str(variable)
                if len(var_name.split("-")) > 2:
                    var_name = var_name.split("-")[2]
                if source_map.is_a_parameter_or_state_variable(var_name):
                    s += "<span style='margin-left: 20px'>" + var_name + " = " + str(asrt.model[variable]) + "</span>" + "<br />"
        else:
            s += "\n%s:%s:%s\n" % (source_map.cname, location['begin']['line'] + 1, location['begin']['column'] + 1)
            s += source_code + "\n"
            s += "^\n"
            for variable in asrt.model.decls():
                var_name = str(variable)
                if len(var_name.split("-")) > 2:
                    var_name = var_name.split("-")[2]
                if source_map.is_a_parameter_or_state_variable(var_name):
                    s += var_name + " = " + str(asrt.model[variable]) + "\n"

    if s:
        results["assertion_failure"] = s
    s = "\t  Assertion failure: \t True" + s if s else "\t  Assertion failure: \t False"
    log.info(s)

def validate_width_conversions():#验证宽度转换
    false_positives = []
    appended = []
    removed = []
    for arithmetic_error in arithmetic_errors:
        if arithmetic_error["instruction"].opcode == "SIGNEXTEND" or arithmetic_error["instruction"].opcode == "AND":#有符号的拓展，和运算
            for width_conversion in width_conversions:
                if is_expr(arithmetic_error["instruction"].data_out[0]) and is_expr(width_conversion):
                    if len(get_vars(arithmetic_error["instruction"].data_out[0])) == 1 and len(get_vars(width_conversion)) == 1:
                        if get_vars(arithmetic_error["instruction"].data_out[0])[0] == get_vars(width_conversion)[0]:
                            if not check_width_conversion(width_conversion, width_conversion, arithmetic_error["instruction"].data_out[0], None, None, None, None, None, None):
                                if not arithmetic_error in false_positives and not arithmetic_error in appended:
                                    false_positives.append(arithmetic_error)
                                appended.append(arithmetic_error)
                            else:
                                if arithmetic_error in false_positives and not arithmetic_error in removed:
                                    false_positives.remove(arithmetic_error)
                                removed.append(arithmetic_error)
    for false_positive in false_positives:
        arithmetic_errors.remove(false_positive)

def detect_arithmetic_errors():#检查运算错误
    global source_map
    global results

    if global_params.DEBUG_MODE:
        print ""
        print "Number of arithmetic errors: "+str(len(arithmetic_errors))
        for error in arithmetic_errors:
            if error["validated"]:
                print error
                print error["instruction"]
                print ""

    arithmetic_bug_found = any([arithmetic_error for arithmetic_error in arithmetic_errors if arithmetic_error["validated"]])
    overflow_bug_found   = any([ErrorTypes.OVERFLOW in arithmetic_error["type"] for arithmetic_error in arithmetic_errors if arithmetic_error["validated"]])
    underflow_bug_found  = any([ErrorTypes.UNDERFLOW in arithmetic_error["type"] for arithmetic_error in arithmetic_errors if arithmetic_error["validated"]])
    division_bug_found   = any([ErrorTypes.DIVISION in arithmetic_error["type"] for arithmetic_error in arithmetic_errors if arithmetic_error["validated"]])
    modulo_bug_found     = any([ErrorTypes.MODULO in arithmetic_error["type"] for arithmetic_error in arithmetic_errors if arithmetic_error["validated"]])
    truncation_bug_found = any([ErrorTypes.WIDTH_CONVERSION in arithmetic_error["type"] for arithmetic_error in arithmetic_errors if arithmetic_error["validated"]])
    signedness_bug_found = any([ErrorTypes.SIGNEDNESS in arithmetic_error["type"] for arithmetic_error in arithmetic_errors if arithmetic_error["validated"]])

    log.info("\t  Arithmetic bugs: \t %s", arithmetic_bug_found)
    if source_map:
        # Overflow bugs
        pcs = [arithmetic_error["pc"] for arithmetic_error in arithmetic_errors if ErrorTypes.OVERFLOW in arithmetic_error["type"] and arithmetic_error["validated"]]
        pcs = [pc for pc in pcs if source_map.find_source_code(pc)]
        pcs = source_map.reduce_same_position_pcs(pcs)
        s = source_map.to_str(pcs, "Overflow bugs")
        if global_params.MODEL_INPUT:
            for pc in pcs:
                if pc in arithmetic_models:
                    s += "\nInput: {"
                    for var in arithmetic_models[pc]:
                        s += "\n  "+str(var)+" = "+str(arithmetic_models[pc][var])
                    s += "\n}"
        if s:
            results["overflow"] = s
        s = "\t  "+u'\u2514'+"> Overflow bugs: \t True" + s if s else "\t  "+u'\u2514'+"> Overflow bugs: \t False"
        log.info(s)
        # Underflow bugs
        pcs = [arithmetic_error["pc"] for arithmetic_error in arithmetic_errors if ErrorTypes.UNDERFLOW in arithmetic_error["type"] and arithmetic_error["validated"]]
        pcs = [pc for pc in pcs if source_map.find_source_code(pc)]
        pcs = source_map.reduce_same_position_pcs(pcs)
        s = source_map.to_str(pcs, "Underflow bugs")
        if global_params.MODEL_INPUT:
            for pc in pcs:
                if pc in arithmetic_models:
                    s += "\nModel: {"
                    for var in arithmetic_models[pc]:
                        s += "\n  "+str(var)+" = "+str(arithmetic_models[pc][var])
                    s += "\n}"
        if s:
            results["underflow"] = s
        s = "\t  "+u'\u2514'+"> Underflow bugs: \t True" + s if s else "\t  "+u'\u2514'+"> Underflow bugs: \t False"
        log.info(s)
        # Division bugs
        pcs = [arithmetic_error["pc"] for arithmetic_error in arithmetic_errors if ErrorTypes.DIVISION in arithmetic_error["type"] and arithmetic_error["validated"]]
        pcs = [pc for pc in pcs if source_map.find_source_code(pc)]
        pcs = source_map.reduce_same_position_pcs(pcs)
        s = source_map.to_str(pcs, "Division bugs")
        if global_params.MODEL_INPUT:
            for pc in pcs:
                if pc in arithmetic_models:
                    s += "\nModel: {"
                    for var in arithmetic_models[pc]:
                        s += "\n  "+str(var)+" = "+str(arithmetic_models[pc][var])
                    s += "\n}"
        if s:
            results["division"] = s
        s = "\t  "+u'\u2514'+"> Division bugs: \t True" + s if s else "\t  "+u'\u2514'+"> Division bugs: \t False"
        log.info(s)
        # Modulo bugs
        pcs = [arithmetic_error["pc"] for arithmetic_error in arithmetic_errors if ErrorTypes.MODULO in arithmetic_error["type"] and arithmetic_error["validated"]]
        pcs = [pc for pc in pcs if source_map.find_source_code(pc)]
        pcs = source_map.reduce_same_position_pcs(pcs)
        s = source_map.to_str(pcs, "Modulo bugs")
        if global_params.MODEL_INPUT:
            for pc in pcs:
                if pc in arithmetic_models:
                    s += "\nModel: {"
                    for var in arithmetic_models[pc]:
                        s += "\n  "+str(var)+" = "+str(arithmetic_models[pc][var])
                    s += "\n}"
        if s:
            results["modulo"] = s
        s = "\t  "+u'\u2514'+"> Modulo bugs:   \t True" + s if s else "\t  "+u'\u2514'+"> Modulo bugs:   \t False"
        log.info(s)
        # Truncation bugs
        pcs = [arithmetic_error["pc"] for arithmetic_error in arithmetic_errors if ErrorTypes.WIDTH_CONVERSION in arithmetic_error["type"] and arithmetic_error["validated"]]
        pcs = [pc for pc in pcs if source_map.find_source_code(pc)]
        pcs = source_map.reduce_same_position_pcs(pcs)
        s = source_map.to_str(pcs, "Truncation bugs")
        if global_params.MODEL_INPUT:
            for pc in pcs:
                if pc in arithmetic_models:
                    s += "\nModel: {"
                    for var in arithmetic_models[pc]:
                        s += "\n  "+str(var)+" = "+str(arithmetic_models[pc][var])
                    s += "\n}"
        if s:
            results["truncation"] = s
        s = "\t  "+u'\u2514'+"> Truncation bugs: \t True" + s if s else "\t  "+u'\u2514'+"> Truncation bugs: \t False"
        log.info(s)
        # Signedness Bugs
        pcs = [arithmetic_error["pc"] for arithmetic_error in arithmetic_errors if ErrorTypes.SIGNEDNESS in arithmetic_error["type"] and arithmetic_error["validated"]]
        pcs = [pc for pc in pcs if source_map.find_source_code(pc)]
        pcs = source_map.reduce_same_position_pcs(pcs)
        s = source_map.to_str(pcs, "Signedness bugs")
        if global_params.MODEL_INPUT:
            for pc in pcs:
                if pc in arithmetic_models:
                    s += "\nModel: {"
                    for var in arithmetic_models[pc]:
                        s += "\n  "+str(var)+" = "+str(arithmetic_models[pc][var])
                    s += "\n}"
        if s:
            results["signedness"] = s
        s = "\t  "+u'\u2514'+"> Signedness bugs: \t True" + s if s else "\t  "+u'\u2514'+"> Signedness bugs: \t False"
        log.info(s)
    else:
        # Overflow bugs
        pcs = [arithmetic_error["pc"] for arithmetic_error in arithmetic_errors if ErrorTypes.OVERFLOW in arithmetic_error["type"] and arithmetic_error["validated"]]
        pcs = set(pcs)
        s = ""
        for pc in pcs:
            for arithmetic_error in arithmetic_errors:
                if arithmetic_error["pc"] == pc:
                    s += "\nOpcode: "+str(arithmetic_error["instruction"].opcode)
                    s += "\nInput: "+str(arithmetic_error["instruction"].data_in)
                    s += "\nOutput: "+str(arithmetic_error["instruction"].data_out)
                    break
            if global_params.MODEL_INPUT:
                if pc in arithmetic_models:
                    s += "\nModel: {"
                    for var in arithmetic_models[pc]:
                        s += "\n  "+str(var)+" = "+str(arithmetic_models[pc][var])
                    s += "\n}"
        if s:
            results["overflow"] = s
        s = "\t  "+u'\u2514'+"> Overflow bugs: \t True" + s if s else "\t  "+u'\u2514'+"> Overflow bugs: \t False"
        log.info(s)
        # Underflow bugs
        pcs = [arithmetic_error["pc"] for arithmetic_error in arithmetic_errors if ErrorTypes.UNDERFLOW in arithmetic_error["type"] and arithmetic_error["validated"]]
        pcs = set(pcs)
        s = ""
        for pc in pcs:
            for arithmetic_error in arithmetic_errors:
                if arithmetic_error["pc"] == pc:
                    s += "\nOpcode: "+str(arithmetic_error["instruction"].opcode)
                    s += "\nInput: "+str(arithmetic_error["instruction"].data_in)
                    s += "\nOutput: "+str(arithmetic_error["instruction"].data_out)
                    break
            if global_params.MODEL_INPUT:
                if pc in arithmetic_models:
                    s += "\nModel: {"
                    for var in arithmetic_models[pc]:
                        s += "\n  "+str(var)+" = "+str(arithmetic_models[pc][var])
                    s += "\n}"
        if s:
            results["underflow"] = s
        s = "\t  "+u'\u2514'+"> Underflow bugs: \t True" + s if s else "\t  "+u'\u2514'+"> Underflow bugs: \t False"
        log.info(s)
        # Division bugs
        pcs = [arithmetic_error["pc"] for arithmetic_error in arithmetic_errors if ErrorTypes.DIVISION in arithmetic_error["type"] and arithmetic_error["validated"]]
        pcs = set(pcs)
        s = ""
        for pc in pcs:
            for arithmetic_error in arithmetic_errors:
                if arithmetic_error["pc"] == pc:
                    s += "\nOpcode: "+str(arithmetic_error["instruction"].opcode)
                    s += "\nInput: "+str(arithmetic_error["instruction"].data_in)
                    s += "\nOutput: "+str(arithmetic_error["instruction"].data_out)
                    break
            if global_params.MODEL_INPUT:
                if pc in arithmetic_models:
                    s += "\nModel: {"
                    for var in arithmetic_models[pc]:
                        s += "\n  "+str(var)+" = "+str(arithmetic_models[pc][var])
                    s += "\n}"
        if s:
            results["division"] = s
        s = "\t  "+u'\u2514'+"> Division bugs: \t True" + s if s else "\t  "+u'\u2514'+"> Division bugs: \t False"
        log.info(s)
        # Modulo bugs
        pcs = [arithmetic_error["pc"] for arithmetic_error in arithmetic_errors if ErrorTypes.MODULO in arithmetic_error["type"] and arithmetic_error["validated"]]
        pcs = set(pcs)
        s = ""
        for pc in pcs:
            for arithmetic_error in arithmetic_errors:
                if arithmetic_error["pc"] == pc:
                    s += "\nOpcode: "+str(arithmetic_error["instruction"].opcode)
                    s += "\nInput: "+str(arithmetic_error["instruction"].data_in)
                    s += "\nOutput: "+str(arithmetic_error["instruction"].data_out)
                    break
            if global_params.MODEL_INPUT:
                if pc in arithmetic_models:
                    s += "\nModel: {"
                    for var in arithmetic_models[pc]:
                        s += "\n  "+str(var)+" = "+str(arithmetic_models[pc][var])
                    s += "\n}"
        if s:
            results["modulo"] = s
        s = "\t  "+u'\u2514'+"> Modulo bugs:   \t True" + s if s else "\t  "+u'\u2514'+"> Modulo bugs:   \t False"
        log.info(s)
        # Truncation bugs
        pcs = [arithmetic_error["pc"] for arithmetic_error in arithmetic_errors if ErrorTypes.WIDTH_CONVERSION in arithmetic_error["type"] and arithmetic_error["validated"]]
        pcs = set(pcs)
        s = ""
        for pc in pcs:
            for arithmetic_error in arithmetic_errors:
                if arithmetic_error["pc"] == pc:
                    s += "\nOpcode: "+str(arithmetic_error["instruction"].opcode)
                    s += "\nInput: "+str(arithmetic_error["instruction"].data_in)
                    s += "\nOutput: "+str(arithmetic_error["instruction"].data_out)
                    break
            if global_params.MODEL_INPUT:
                if pc in arithmetic_models:
                    s += "\nModel: {"
                    for var in arithmetic_models[pc]:
                        s += "\n  "+str(var)+" = "+str(arithmetic_models[pc][var])
                    s += "\n}"
        if s:
            results["truncation"] = s
        s = "\t  "+u'\u2514'+"> Truncation bugs: \t True" + s if s else "\t  "+u'\u2514'+"> Truncation bugs: \t False"
        log.info(s)
        # Signedness Bugs
        pcs = [arithmetic_error["pc"] for arithmetic_error in arithmetic_errors if ErrorTypes.SIGNEDNESS in arithmetic_error["type"] and arithmetic_error["validated"]]
        pcs = set(pcs)
        s = ""
        for pc in pcs:
            for arithmetic_error in arithmetic_errors:
                if arithmetic_error["pc"] == pc:
                    s += "\nOpcode: "+str(arithmetic_error["instruction"].opcode)
                    s += "\nInput: "+str(arithmetic_error["instruction"].data_in)
                    s += "\nOutput: "+str(arithmetic_error["instruction"].data_out)
                    break
            if global_params.MODEL_INPUT:
                if pc in arithmetic_models:
                    s += "\nModel: {"
                    for var in arithmetic_models[pc]:
                        s += "\n  "+str(var)+" = "+str(arithmetic_models[pc][var])
                    s += "\n}"
        if s:
            results["signedness"] = s
        s = "\t  "+u'\u2514'+"> Signedness bugs: \t True" + s if s else "\t  "+u'\u2514'+"> Signedness bugs: \t False"
        log.info(s)
def detect_bugs():
    if isTesting():
        return

    global results
    global g_timeout
    global source_map
    global visited_pcs
    global global_problematic_pcs

    if global_params.DEBUG_MODE:
        print "Number of total paths: "+str(total_no_of_paths)
        print ""

    if instructions:
        evm_code_coverage = float(len(visited_pcs)) / len(instructions.keys()) * 100
        log.info("\t  EVM code coverage: \t %s%%", round(evm_code_coverage, 1))
        results["evm_code_coverage"] = str(round(evm_code_coverage, 1))

        dead_code = list(set(instructions.keys()) - set(visited_pcs))
        for pc in dead_code:
            results["dead_code"].append(instructions[pc])

        validate_width_conversions()

        detect_arithmetic_errors()

        log.debug("Checking for Callstack attack...")
        detect_callstack_attack()

        if global_params.REPORT_MODE:
            rfile.write(str(total_no_of_paths) + "\n")

        detect_money_concurrency()
        detect_time_dependency()

        if global_params.DATA_FLOW:
            detect_data_concurrency()
            detect_data_money_concurrency()

        log.debug("Results for Reentrancy Bug: " + str(reentrancy_all_paths))
        detect_reentrancy()

        if global_params.CHECK_ASSERTIONS:
            if source_map:
                detect_assertion_failure()
            else:
                raise Exception("Assertion checks need a Source Map")

        stop_time = time.time()
        results["execution_time"] = str(stop_time-start_time)
        if global_params.REPORT_MODE:
            rfile.write(str(stop_time-start_time))
            rfile.close()
        log.info("\t --- "+str(stop_time - start_time)+" seconds ---")

        results["execution_paths"] = str(total_no_of_paths)
        results["timeout"] = g_timeout
    else:
        log.info("\t  EVM code coverage: \t 0.0")
        log.info("\t  Arithmetic bugs: \t False")
        log.info("\t  "+u'\u2514'+"> Overflow bugs: \t False")
        log.info("\t  "+u'\u2514'+"> Underflow bugs: \t False")
        log.info("\t  "+u'\u2514'+"> Division bugs: \t False")
        log.info("\t  "+u'\u2514'+"> Modulo bugs:   \t False")
        log.info("\t  "+u'\u2514'+"> Truncation bugs: \t False")
        log.info("\t  "+u'\u2514'+"> Signedness bugs: \t False")
        log.info("\t  Callstack bug: \t False")
        log.info("\t  Concurrency bug: \t False")
        log.info("\t  Time dependency bug: \t False")
        log.info("\t  Reentrancy bug: \t False")
        log.info("\t  --- 0.0 seconds ---")
        if global_params.CHECK_ASSERTIONS:
            log.info("\t  Assertion failure: \t False")
        results["evm_code_coverage"] = "0.0"
        results["execution_paths"] = str(total_no_of_paths)
        results["timeout"] = g_timeout

    if global_params.WEB:
        results_for_web()

def closing_message():
    global c_name
    global results

    log.info("\t====== Analysis Completed ======")
    if global_params.STORE_RESULT:
        result_file = os.path.join(global_params.RESULTS_DIR, c_name+'.json'.split('/')[-1])
        if '.sol' in c_name:
            result_file = os.path.join(global_params.RESULTS_DIR, c_name.split(':')[0].replace('.sol', '.json').split('/')[-1])
        elif '.bin.evm.disasm' in c_name:
            result_file = os.path.join(global_params.RESULTS_DIR, c_name.replace('.bin.evm.disasm', '.json').split('/')[-1])
        if not os.path.isfile(result_file):
            with open(result_file, 'a') as of:
                if ':' in c_name:
                    of.write("{")
                    of.write('"'+str(c_name.split(':')[1].replace('.evm.disasm', ''))+'":')
                of.write(json.dumps(results, indent=1))
        else:
            with open(result_file, 'a') as of:
                if ':' in c_name:
                    of.write(",")
                    of.write('"'+str(c_name.split(':')[1].replace('.evm.disasm', ''))+'":')
                of.write(json.dumps(results, indent=1))
        log.info("Wrote results to %s.", result_file)

def handler(signum, frame):
    global g_timeout

    if global_params.UNIT_TEST == 2 or global_params.UNIT_TEST == 3:#如果是单元测试则退出
        exit(TIME_OUT)
    print "!!! SYMBOLIC EXECUTION TIMEOUT !!!"#符号执行超时处理函数
    g_timeout = True
    raise Exception("timeout")#报错

def results_for_web():
    global results

    results["filename"] = source_map.cname.split(":")[0].split("/")[-1]
    results["cname"] = source_map.cname.split(":")[1]
    print "======= results ======="
    print json.dumps(results)

def main(contract, contract_sol, _source_map = None):
    global c_name
    global c_name_sol
    global source_map
    global validator
    global start_time

    c_name = contract #'datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory.evm.disasm'
    c_name_sol = contract_sol #'datasets/SimpleDAO/SimpleDAO_0.4.19.sol'
    source_map = _source_map
    validator = Validator(source_map)

    check_unit_test_file()
    initGlobalVars()
    set_cur_file(c_name[4:] if len(c_name) > 5 else c_name)#'sets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory.evm.disasm'
    start_time = time.time()
    if hasattr(signal, 'SIGALRM'):#如果有设置超时阈值则调用相关的超时处理函数
        signal.signal(signal.SIGALRM, handler)#signal.signal() 函数允许定义在接收到信号时执行的自定义处理程序。
        if global_params.UNIT_TEST == 2 or global_params.UNIT_TEST == 3:
            global_params.GLOBAL_TIMEOUT = global_params.GLOBAL_TIMEOUT_TEST
        signal.alarm(global_params.GLOBAL_TIMEOUT)#global_params.GLOBAL_TIMEOUT：50，设置更大的方便调试
        #global_params.GLOBAL_TIMEOUT时间后，触发SIGALRM信号，触发处理程序
    log.info("Running, please wait...")

    init_taint_analysis()

    try:
        build_cfg_and_analyze()
        log.debug("Done Symbolic execution")#如果log4j的配置中开启debug级别日志，那么我们就打印输出debug日志，其在输出日志中会被标记为[DEBUG].
    except Exception as e:
        if global_params.UNIT_TEST == 2 or global_params.UNIT_TEST == 3:
            log.exception(e)
            exit(EXCEPTION)
        if global_params.DEBUG_MODE:
            traceback.print_exc()
        if str(e) == "timeout":
            pass
        else:
            raise e

    if callable(getattr(signal, "alarm", None)):
        signal.alarm(0)

    if not isTesting():
        log.info("\t============ Results ===========")

    detect_bugs()
    closing_message()

if __name__ == '__main__':
    main(sys.argv[1])
