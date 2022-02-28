class AstWalker:
    def walk(self, node, node_name, nodes):#node_name:'FunctionCall',nodes:[],node['name']:u'ContractDefinition'
        if node["name"] == node_name:
            nodes.append(node)#往nodes列表中添加查找到的节点类型是node_name的节点
        else:
            if "children" in node and node["children"]:#如果要求查找的node_name和node实际的name不符合，则递归查找该节点的子节点，将符合node_name的节点加入nodes列表
                for child in node["children"]:
                    self.walk(child, node_name, nodes)#递归查找要求的node_name
