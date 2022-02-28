import json
import global_params
import ast
from utils import run_command
from ast_helper import AstHelper

class Source:
    def __init__(self, filename):
        self.filename = filename
        self.content = self.__load_content()
        self.line_break_positions = self.__load_line_break_positions()#换行符的字符位置

    def __load_content(self):
        with open(self.filename, 'r') as f:
            content = f.read()
        return content

    def __load_line_break_positions(self):
        return [i for i, letter in enumerate(self.content) if letter == '\n']

class SourceMap:
    parent_filename = ""
    position_groups = {}
    sources = {}
    ast_helper = None

    def __init__(self, cname, parent_filename):
        self.cname = cname
        if not SourceMap.parent_filename:
            SourceMap.parent_filename = parent_filename
            SourceMap.position_groups = SourceMap.__load_position_groups()#得到反汇编后的对象
            SourceMap.ast_helper = AstHelper(SourceMap.parent_filename)#parent_filename是sourcemap的源文件，即合约sol文件
        self.source = self.__get_source()
        self.positions = self.__get_positions()
        self.instr_positions = {}#指令位置
        self.var_names = self.__get_var_names()
        self.func_call_names = self.__get_func_call_names()

    def find_source_code(self, pc):
        try:
            pos = self.instr_positions[pc]
        except:
            return ""
        begin = pos['begin']#开始的字符位置
        end = pos['end']
        return self.source.content[begin:end]#源代码

    def to_str(self, pcs, bug_name):
        s = ""
        for pc in pcs:
            source_code = self.find_source_code(pc).split("\n", 1)[0]#self.find_source_code(pc).split("\n", 1)：要分析的代码的第一行和剩余的行
            if not source_code:
                continue

            location = self.get_location(pc)#行号列号
            if global_params.WEB:
                s += "%s:%s:%s: %s:<br />" % (self.cname.split(":", 1)[1], location['begin']['line'] + 1, location['begin']['column'] + 1, bug_name)#报告行号列号和bug名
                s += "<span style='margin-left: 20px'>%s</span><br />" % source_code#s是html的形式
                s += "<span style='margin-left: 20px'>^</span><br />"
            else:
                s += "\n%s:%s:%s\n" % (self.cname, location['begin']['line'] + 1, location['begin']['column'] + 1)
                s += source_code + "\n"#s是字符串形式
                s += "^"
        return s

    def get_location(self, pc):
        pos = self.instr_positions[pc]#当前指令的位置对象，有begin，end参数
        return self.__convert_offset_to_line_column(pos)#将位置转换为行号列号

    def reduce_same_position_pcs(self, pcs):
        d = {}
        for pc in pcs:
            pos = str(self.instr_positions[pc])
            if pos not in d:
                d[pos] = pc
        return d.values()

    def is_a_parameter_or_state_variable(self, var_name):
        try:
            names = [
                node.id for node in ast.walk(ast.parse(var_name))
                if isinstance(node, ast.Name)
            ]
            if names[0] in self.var_names:
                return True
        except:
            return False
        return False

    def __get_source(self):
        fname = self.__get_filename()#文件名,fname=datasets/SimpleDAO/SimpleDAO_0.4.19.sol
        if SourceMap.sources.has_key(fname):#SourceMap.sources:{'datasets/SimpleDAO/SimpleDAO_0.4.19.sol': <source_map.Source instance at 0x7eff52a68170>}，一个文件字符串和sourcemap对象的字典
            return SourceMap.sources[fname]#返回值是<source_map.Source instance at 0x7f5f2a922098>
        else:
            SourceMap.sources[fname] = Source(fname)
            return SourceMap.sources[fname]

    def __get_var_names(self):#self.cname:datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory
        return SourceMap.ast_helper.extract_state_variable_names(self.cname)#合约的变量名列表

    def __get_func_call_names(self):
        func_call_srcs = SourceMap.ast_helper.extract_func_call_srcs(self.cname)
        func_call_names = []
        for src in func_call_srcs:
            src = src.split(":")
            start = int(src[0])
            end = start + int(src[1])
            func_call_names.append(self.source.content[start:end])
        #print(func_call_names)
        return func_call_names#func_call_names['owner.send(this.balance)', 'dao.withdraw(dao.queryCredit(this))']
        #具体值func_call_names['dao.donate.value(1)(this)', 'dao.withdraw(1)', 'dao.withdraw(dao.balance)', 'owner.send(this.balance)', 'dao.withdraw(1)']

    @classmethod
    def __load_position_groups(cls):
        cmd = "solc --combined-json asm %s" % cls.parent_filename
        out = run_command(cmd)
        out = json.loads(out)
        #print(out['contracts'])是汇编指令对象
        return out['contracts']

    def __get_positions(self):
        asm = SourceMap.position_groups[self.cname]['asm']['.data']['0']#self.cname：'datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory'
        positions = asm['.code']#positions是list，asm是len为2的dict
        while(True):
            try:
                positions.append(None)
                positions += asm['.data']['0']['.code']#没有data则会报错，跳出循环，返回分析好的evm汇编指令列表（positions）
                asm = asm['.data']['0']
            except:
                break
        return positions#分段分析反汇编代码，先大段再小段

    def __convert_offset_to_line_column(self, pos):#将偏移量转化为行号列号
        ret = {}
        ret['begin'] = None
        ret['end'] = None
        if pos['begin'] >= 0 and (pos['end'] - pos['begin'] + 1) >= 0:
            ret['begin'] = self.__convert_from_char_pos(pos['begin'])#开始的字符位置的行号列号
            ret['end'] = self.__convert_from_char_pos(pos['end'])#结束字符位置的行号列号
        return ret

    def __convert_from_char_pos(self, pos):
        line = self.__find_lower_bound(pos, self.source.line_break_positions)#line是最近的换行符的在数组中的下标
        if self.source.line_break_positions[line] != pos:#寻找对应pos的换行符的在数组中的下标
            line += 1
        begin_col = 0 if line == 0 else self.source.line_break_positions[line - 1] + 1
        col = pos - begin_col#计算列号
        return {'line': line, 'column': col}

    def __find_lower_bound(self, target, array):#target是pos，array是换行符字符位置，找指定位置的最近换行符
        start = 0
        length = len(array)#数组长度
        while length > 0:
            half = length >> 1#长度的一半
            middle = start + half#中间的数组位置
            if array[middle] <= target:#数组中间的换行符位置和指定位置比较,如果换行符位置比当前位置小，就将寻找的起点增大，start是最近的换行符在换行符数组中的下标的更大一个自然数
                length = length - 1 - half#
                start = middle + 1
            else:
                length = half
        return start - 1

    def __get_filename(self):
        return self.cname.split(":")[0]#文件名
