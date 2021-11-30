# this the interface to create your own data source 
# this class pings etherscan to get the latest code and balance information
#此接口用于创建自己的数据源
#这个类ping etherscan以获取最新的代码和平衡信息

import json
import re
import requests

class EthereumData:
	def __init__(self):
		self.apiDomain = "https://api.etherscan.io/api"
		self.apikey = "VT4IW6VK7VES1Q9NYFI74YKH8U7QW9XRHN"

	def getBalance(self, address):#获取指定地址的合约的余额
		apiEndPoint = self.apiDomain + "?module=account&action=balance&address=" + address + "&tag=latest&apikey=" + self.apikey
		r = requests.get(apiEndPoint)#发出request请求,返回对象
		result = json.loads(r.text)#
		status = result['message']
		if status == "OK":
			return result['result']#判断api请求是否成功
		return -1

	def getCode(self, address):#获取合约的字节码
		# apiEndPoint = self.apiDomain + "" + address + "&tag=latest&apikey=" + apikey
		# no direct endpoint for this
		r = requests.get("https://etherscan.io/address/" + address + "#code")
		html = r.text
		code = re.findall("<div id='verifiedbytecode2'>(\w*)<\/div>", html)[0]
		return code