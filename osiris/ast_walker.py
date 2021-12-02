class AstWalker:
    def walk(self, node, node_name, nodes):
        if node["name"] == node_name:
            nodes.append(node)#往nodes列表中添加查找到的节点类型是node_name的节点
        else:
            if "children" in node and node["children"]:#递归查找该节点的子节点
                for child in node["children"]:
                    self.walk(child, node_name, nodes)#递归
