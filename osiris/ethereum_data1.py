# this the interface to create your own data source 
# this class pings a private / public blockchain to get the balance and code information 
#此接口用于创建自己的数据源
#这个类ping一个私有/公共的区块链来获取余额和代码信息
from web3 import Web3, KeepAliveRPCProvider

class EthereumData:
	def __init__(self):
		self.host = 'x.x.x.x'#个人私有链创建的合约余额和字节码
		self.port = '8545'
		self.web3 = Web3(KeepAliveRPCProvider(host=self.host, port=self.port))		

	def getBalance(self, address):
		return self.web3.eth.getBalance(address)

	def getCode(self, address):		
		return self.web3.eth.getCode(address)