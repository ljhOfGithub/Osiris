from utils import run_command
from ast_walker import AstWalker
import json

class AstHelper:
    def __init__(self, filename):
        self.source_list = self.get_source_list(filename)
        self.contracts = self.extract_contract_definitions(self.source_list)

    def get_source_list(self, filename):
        cmd = "solc --combined-json ast %s" % filename
        out = run_command(cmd)
        out = json.loads(out)
        #print("out[sources]:"out["sources"])
        #print("\n")
        return out["sources"]


    def extract_contract_definitions(self, sourcesList):
        ret = {
            "contractsById": {},
            "contractsByName": {},
            "sourcesByContract": {}
        }
        walker = AstWalker()
        for k in sourcesList:#需要分析的合约列表，通过命令行参数指定合约文件
            nodes = []
            #print(sourcesList[k]["AST"])
            walker.walk(sourcesList[k]["AST"], "ContractDefinition", nodes)#sourcesList[k]["AST"]类似u'attributes'的ast
            for node in nodes:
                ret["contractsById"][node["id"]] = node#
                ret["sourcesByContract"][node["id"]] = k#sourcesByContract收集合约在ast树中的节点编号
                ret["contractsByName"][k + ':' + node["attributes"]["name"]] = node#在ret中存储整个合约节点
        #print(nodes)
        return ret

    def get_linearized_base_contracts(self, id, contractsById):#获得linearizedBaseContracts列表与原合约构成的map数据结构
        return map(lambda id: contractsById[id], contractsById[id]["attributes"]["linearizedBaseContracts"])#contractsById[id]["attributes"]["linearizedBaseContracts"]是[204]，是列表
        #lambda表达式里面使用列表中的每个元素获得contractsById[id]，contractsById[id]等价于self.contracts["contractsById"][204]

    def extract_state_definitions(self, c_name):#指定合约名，举例cname：u'datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2'
        node = self.contracts["contractsByName"][c_name]#长度为5的字典，包括attributes，src，children长度是7，name是ContractDefinition，id是204
        state_vars = []#self.contracts["contractsByName"][c_name]['children'][1]如下：
# attributes
# {u'storageLocation': u'default', u'constant': False, u'name': u'owner', u'stateVariable': True, u'value': None, u'visibility': u'internal', u'scope': 204, u'type': u'address'}
# src
# 877:13:0
# children
# [{u'attributes': {u'type': u'address', u'name': u'address'}, u'src': u'877:7:0', u'id': 120, u'name': u'ElementaryTypeName'}]
# name
# VariableDeclaration
# id
# 121
        if node:#
            base_contracts = self.get_linearized_base_contracts(node["id"], self.contracts["contractsById"])#根据某个合约找到linearizedBaseContracts列表
            base_contracts = list(reversed(base_contracts))#此例中只有一个合约
            for contract in base_contracts:#contract长度是5，是字典
                if "children" in contract:#基础合约列表的子节点的变量声明节点
                    for item in contract["children"]:#此例中没有children
                        if item["name"] == "VariableDeclaration":
                            state_vars.append(item)#VariableDeclaration节点有：变量类型和涉及的函数列表，添加VariableDeclaration节点
        return state_vars

    def extract_states_definitions(self):#self:<ast_helper.AstHelper instance at 0x7eff52c19908>
        ret = {}#self.contracts["contractsById"]是字典，self.contracts["contractsById"].keys()：[204, 68, 117]是合约的id
        for contract in self.contracts["contractsById"]:#contractsById下面是合约节点列表，contract是合约节点的编号（通过print得到的）
            name = self.contracts["contractsById"][contract]["attributes"]["name"]#204对应Mallory2，68对应SimpleDAO，117对应Mallory，合约名
            source = self.contracts["sourcesByContract"][contract]#三个合约id对应同一个solidity文件
            full_name = source + ":" + name#规范完整可用于查找的合约名，全名是solidity文件名加合约名
            ret[full_name] = self.extract_state_definitions(full_name)#找到合约名才能找，构造合约全名和合约变量声明节点的字典
        return ret#长3的字典

    def extract_func_call_definitions(self, c_name):#c_name:u'datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2'
        node = self.contracts["contractsByName"][c_name]#查找某个节点下面的FunctionCall节点
        walker = AstWalker()#类，不是方法，<ast_walker.AstWalker instance at 0x7eff52a9e710>
        nodes = []
        if node:
            walker.walk(node, "FunctionCall", nodes)#往nodes中添加FunctionCall节点
        return nodes

    def extract_func_calls_definitions(self):
        ret = {}
        for contract in self.contracts["contractsById"]:#遍历所有合约节点的FunctionCall节点,self.contracts["contractsById"]长3，self.contracts["contractsById"].keys()：[204, 68, 117]
            name = self.contracts["contractsById"][contract]["attributes"]["name"]#u'Mallory2'
            source = self.contracts["sourcesByContract"][contract]#u'datasets/SimpleDAO/SimpleDAO_0.4.19.sol'
            full_name = source + ":" + name#建立合约全名和合约函数调用的定义，u'datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory2'
            ret[full_name] = self.extract_func_call_definitions(full_name)
        return ret

    def extract_state_variable_names(self, c_name):#c_name='datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory'
        state_variables = self.extract_states_definitions()[c_name]#合约Mallory的合约变量声明节点的字典，state_variables长2
        var_names = []
        for var_name in state_variables:
            var_names.append(var_name["attributes"]["name"])#具体值：var_names：[u'dao', u'owner', u'performAttack']
        return var_names#var_names：[u'dao', u'owner']

    def extract_func_call_srcs(self, c_name):#c_name:'datasets/SimpleDAO/SimpleDAO_0.4.19.sol:Mallory'
        func_calls = self.extract_func_calls_definitions()[c_name]#指定合约的FunctionCall节点，func_calls长2
        func_call_srcs = []#合约调用的位置列表，三元组，Mallory调用send函数，withdraw函数
        for func_call in func_calls:
            func_call_srcs.append(func_call["src"])#指定合约的所有FunctionCall节点的source位置，即合约调用其他函数的源码位置
        return func_call_srcs
