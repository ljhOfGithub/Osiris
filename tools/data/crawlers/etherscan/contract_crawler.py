#!/usr/bin/python
# -*- coding: utf-8 -*-

import Queue
import threading
import pymongo
import datetime

from opcodes import *# opcodes[name]有一个[value (index)，从堆栈中删除的项数，添加到堆栈中的项数]的列表
from web3 import Web3, KeepAliveRPCProvider
from bson.decimal128 import Decimal128
from pymongo import MongoClient

web3 = Web3(KeepAliveRPCProvider(host='127.0.0.1', port='8545'))
latestBlock = web3.eth.getBlock('latest')#最新区块
exitFlag = 0

nrOfTransactions = {}

def init():
    if web3.eth.syncing == False:#还没更新到最新的区块链状态
        print('Ethereum blockchain is up-to-date.')#上次更新到的区块链状态
        print('Latest block: '+str(latestBlock.number)+' ('+datetime.datetime.fromtimestamp(int(latestBlock.timestamp)).strftime('%d-%m-%Y %H:%M:%S')+')\n')
    else:
        print('Ethereum blockchain is currently syncing...')
        print('Latest block: '+str(latestBlock.number)+' ('+datetime.datetime.fromtimestamp(int(latestBlock.timestamp)).strftime('%d-%m-%Y %H:%M:%S')+')\n')

class searchThread(threading.Thread):#多线程
   def __init__(self, threadID, queue, collection):
      threading.Thread.__init__(self)
      self.threadID = threadID
      self.queue = queue
      self.collection = collection
   def run(self):
      searchContract(self.queue, self.collection)

def searchContract(queue, collection):
    while not exitFlag:
        queueLock.acquire()#accquire()申请锁，release()释放锁
        if not queue.empty():
            blockNumber = queue.get()#阻塞程序，等待队列消息。从队列中取到需要爬的区块号后解队列锁，队列是临界资源
            #Queue.get(block=True, timeout=None)
            #从队列中移除并返回一个项目。
            #如果可选参数 block 是 true 并且 timeout 是 None (默认值)，则在必要时阻塞至项目可得到。
            #如果 timeout 是个正数，将最多阻塞 timeout 秒，如果在这段时间内项目不能得到，将引发 Empty 异常。
            #反之 (block 是 false) , 如果一个项目立即可得到，则返回一个项目，否则引发 Empty 异常 (这种情况下，timeout 将被忽略)。
            queueLock.release()
            print('Searching block '+str(blockNumber)+' for contracts...')
            block = web3.eth.getBlock(blockNumber, True)#返回块号或区块哈希值所对应的区块，true会将区块包含的所有交易作为对象返回。否则只返回交易的哈希。
            if block and block.transactions:#非空区块
                for transaction in block.transactions:
                    if not transaction.to:#有交易对象的地址
                        receipt = web3.eth.getTransactionReceipt(transaction.hash)#交易收据对象
                        result = collection.find({'address': receipt['contractAddress']})#查找地址是所求合约地址receipt['contractAddress']的合约对象
                        print('Contract found: '+receipt['contractAddress'])#打印找到的合约地址
                        if result.count() == 0:#没有找到合约则将合约中相关的0x去掉
                            transaction_input = transaction['input'].replace("0x", "")#input:交易附带的数据
                            contract_code = web3.eth.getCode(receipt['contractAddress']).replace("0x", "")#给定地址合约编译后的字节代码
                            # Uncomment this line if you want to skip zombie contracts如果您想跳过僵尸契约，请取消注释这一行
                            #if len(transaction_input) == 0 and len(contract_code) == 0:
                            #    print('Contract '+receipt['contractAddress']+' is empty...')
                            #    continue
                            contract = {}#创建合约对象
                            contract['address'] = receipt['contractAddress']#如果是一个合约创建交易，返回合约地址，其它情况返回null。
                            contract['transactionHash'] = transaction['hash']
                            contract['blockNumber'] = transaction['blockNumber']
                            contract['timestamp'] = block.timestamp
                            contract['creator'] = transaction['from']#合约发起方是合约的创建者
                            contract['input'] = transaction_input
                            contract['byteCode'] = contract_code
                            contract['balance'] = web3.fromWei(web3.eth.getBalance(contract['address']), 'ether')
                            if not contract['balance'] == 0:#防止出现关于正零和负零的差别
                                contract['balance'] = Decimal128(contract['balance'])
                            else:
                                contract['balance'] = Decimal128('0')
                            contract['nrOfTransactions'] = 0#the number of transactions；nr=number；合约涉及的交易数初始化为0
                            instructions = getInstructions(contract_code)
                            contract['nrOfInstructions'] = len(instructions)
                            contract['nrOfDistinctInstructions'] = len(set(instructions))#指令去重计数
                            collection.insert_one(contract)#合约对象组成的mongo的collection对象
                            # Indexing...
                            if 'address' not in collection.index_information():#返回索引相关信息构成的字典https://www.osgeo.cn/mongo-python-driver/api/pymongo/collection.html
                                collection.create_index('address', unique=True)#索引中没有地址信息，则添加作为合约的索引之一
                                collection.create_index('transactionHash', unique=True)
                                collection.create_index('blockNumber')#合约所在的区块号
                                collection.create_index('timestamp')
                                collection.create_index('creator')
                                collection.create_index('balance')
                                collection.create_index('nrOfTransactions')
                                collection.create_index('nrOfInstructions')
                                collection.create_index('nrOfDistinctInstructions')
                            print('Contract '+contract['address']+' has been successfully added.')
                        else:
                            print('Contract '+receipt['contractAddress']+' already exists...')
                    transactionLock.acquire()#加锁
                    if transaction['from'] in nrOfTransactions:#有该合约，该合约计数+1，涉及该合约的交易数字典，key是合约地址，value是合约发起的交易数
                        nrOfTransactions[transaction['from']] += 1#
                    else:#没有则初始化为1
                        nrOfTransactions[transaction['from']] = 1
                    if transaction['to'] in nrOfTransactions:#合约收到的交易数
                        nrOfTransactions[transaction['to']] += 1
                    else:
                        nrOfTransactions[transaction['to']] = 1
                    transactionLock.release()
        else:
            queueLock.release()

def getInstructions(byteCode):
    code = bytearray.fromhex(byteCode)
    pc = 0
    instructions = []#列表
    while pc < len(code):#遍历字节码反汇编生成的汇编指令
        try:
            currentOpCode = opcodes[code[pc]][0]#code[pc]某个位置的汇编指令
            instructions.append(currentOpCode)#加入汇编指令列表
            if (currentOpCode[0:4] == 'PUSH'):
                pc += int(currentOpCode[4:])#更新pc，根据push的位数具体修改
        except Exception:
            instructions.append('INVALID OPCODE '+hex(code[pc]))#抛出错误，无效的指令操作码
            pass
        pc += 1
    return instructions

if __name__ == "__main__":
    init()

    transactionLock = threading.Lock()#线程加锁,生成锁对象，全局唯一

    queueLock = threading.Lock()#队列加锁
    queue = Queue.Queue()

    # Create new threads
    threads = []
    threadID = 1
    for i in range(1000):#1000个线程
        collection = MongoClient('127.0.0.1', 27017)['ethereum']['contracts']#mongodb数据库
        thread = searchThread(threadID, queue, collection)
        thread.start()#线程开始
        threads.append(thread)#线程对象列表
        threadID += 1#线程id

    startBlockNumber = 0#开始的区块号
    #cursor = MongoClient('127.0.0.1', 27017)['ethereum']['contracts'].find().sort('blockNumber', pymongo.DESCENDING).limit(1)
    #for contract in cursor:
    #    startBlockNumber = contract['blockNumber']
    #endBlockNumber = max(startBlockNumber, latestBlock.number)
    endBlockNumber = 5000000

    # Fill the queue with block numbers
    queueLock.acquire()#队列加锁
    for i in range(startBlockNumber, endBlockNumber+1):
        queue.put(i)#添加区块号到队列中
    queueLock.release()#释放队列锁
#Queue队列，这是从一个线程向另一个线程发送数据最安全的方式。创建一个被多个线程共享的 Queue 对象，这些线程通过使用put() 和 get() 操作来向队列中添加或者删除元素。
    print('Searching for contracts within blocks '+str(startBlockNumber)+' and '+str(endBlockNumber)+'\n')

    # Wait for queue to empty
    while not queue.empty():#直到队列中所有的
        pass

    # Notify threads it's time to exit通知线程退出
    exitFlag = 1

    # Wait for all threads to complete等待所有线程完成
    for t in threads:
       t.join()

    # Copy number of transactions to database将事务数复制到数据库
    collection = MongoClient('127.0.0.1', 27017)['ethereum']['contracts']
    cursor = collection.find()
    for contract in cursor:
        if contract['address'] in nrOfTransactions:
            contract['nrOfTransactions'] = nrOfTransactions[contract['address']]
            collection.save(contract)

    print('\nDone')
