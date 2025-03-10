import os
import sys
import time
import logging
import requests
from datetime import datetime
from urllib.parse import urlparse
from web3 import Web3
from colorama import init, Fore

# 初始化 colorama（可选，用于终端彩色日志）
init(autoreset=True)

# 配置日志记录（同时输出到终端）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

# 从环境变量或默认值读取配置
RPC_URL = os.getenv("RPC_URL", "https://rpc.testnet.humanity.org")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS", "0xa18f6FCB2Fd4884436d10610E69DB7BFa1bFe8C7")
PRIVATE_KEYS_FILE = os.getenv("PRIVATE_KEYS_FILE", "private_keys.txt")
PROXY_FILE = os.getenv("PROXY_FILE", "proxy.txt")

# 合约 ABI（请根据实际情况替换或加载完整 ABI）
CONTRACT_ABI = [
    {"inputs":[],"name":"AccessControlBadConfirmation","type":"error"},
    {"inputs":[{"internalType":"address","name":"account","type":"address"},{"internalType":"bytes32","name":"neededRole","type":"bytes32"}],"name":"AccessControlUnauthorizedAccount","type":"error"},
    # ... 此处省略其他 ABI 定义 ...
]

class HumanityProtocolBot:
    def __init__(self):
        self.rpc_url = RPC_URL
        self.contract_address = CONTRACT_ADDRESS
        self.contract_abi = CONTRACT_ABI

    @staticmethod
    def current_time():
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def load_accounts_data(self):
        """加载私钥和对应的代理"""
        accounts_data = []
        try:
            with open(PRIVATE_KEYS_FILE, 'r') as f:
                private_keys = [line.strip() for line in f if line.strip()]
        except FileNotFoundError:
            logging.error("错误: 找不到 %s 文件", PRIVATE_KEYS_FILE)
            sys.exit(1)

        try:
            with open(PROXY_FILE, 'r') as f:
                proxies = [line.strip() for line in f if line.strip()]
        except FileNotFoundError:
            logging.warning("未找到 %s 文件，所有账号将使用直连", PROXY_FILE)
            proxies = [''] * len(private_keys)

        if len(proxies) < len(private_keys):
            logging.warning("代理数量(%d)少于私钥数量(%d)，部分账号将使用直连", len(proxies), len(private_keys))
            proxies.extend([''] * (len(private_keys) - len(proxies)))

        for pk, proxy in zip(private_keys, proxies):
            accounts_data.append({
                'private_key': pk,
                'proxy': proxy
            })
        return accounts_data

    @staticmethod
    def format_proxy(proxy):
        """格式化代理字符串"""
        if not proxy:
            return None
        try:
            if proxy.startswith('socks5://'):
                return {'http': proxy, 'https': proxy}
            elif proxy.startswith('http://') or proxy.startswith('https://'):
                return {'http': proxy, 'https': proxy}
            else:
                return {'http': f'http://{proxy}', 'https': f'http://{proxy}'}
        except Exception as e:
            logging.error("代理格式化错误: %s", str(e))
            return None

    def setup_blockchain_connection(self, proxy=None):
        """建立区块链连接"""
        try:
            if proxy:
                formatted_proxy = self.format_proxy(proxy)
                if formatted_proxy:
                    session = requests.Session()
                    session.proxies = formatted_proxy
                    web3_instance = Web3(Web3.HTTPProvider(
                        self.rpc_url,
                        session=session,
                        request_kwargs={"timeout": 30}
                    ))
                else:
                    web3_instance = Web3(Web3.HTTPProvider(self.rpc_url))
            else:
                web3_instance = Web3(Web3.HTTPProvider(self.rpc_url))

            if web3_instance.is_connected():
                logging.info("%s 成功连接到区块链 (代理: %s)", self.current_time(), proxy or "直连")
                return web3_instance
            else:
                logging.error("连接失败: 无法连接到 %s", self.rpc_url)
                return None
        except Exception as e:
            logging.error("连接错误: %s", str(e))
            return None

    def claim_rewards(self, private_key, web3_instance, contract):
        """尝试领取奖励"""
        try:
            account = web3_instance.eth.account.from_key(private_key)
            sender_address = account.address
            genesis_claimed = contract.functions.userGenesisClaimStatus(sender_address).call()
            current_epoch = contract.functions.currentEpoch().call()
            buffer_amount, claim_status = contract.functions.userClaimStatus(sender_address, current_epoch).call()

            if (genesis_claimed and not claim_status) or (not genesis_claimed):
                logging.info("为地址 %s 领取奖励", sender_address)
                self.process_claim(sender_address, private_key, web3_instance, contract)
            else:
                logging.info("地址 %s 当前纪元 %s 的奖励已领取", sender_address, current_epoch)
        except Exception as e:
            addr = locals().get("sender_address", "未知")
            logging.error("处理地址 %s 时发生错误: %s", addr, str(e))

    def process_claim(self, sender_address, private_key, web3_instance, contract):
        """处理领取奖励的交易"""
        try:
            nonce = web3_instance.eth.get_transaction_count(sender_address)
            gas_price = web3_instance.eth.gas_price
            gas_estimate = contract.functions.claimReward().estimate_gas({
                'chainId': web3_instance.eth.chain_id,
                'from': sender_address,
                'gasPrice': gas_price,
                'nonce': nonce
            })
            transaction = contract.functions.claimReward().build_transaction({
                'chainId': web3_instance.eth.chain_id,
                'from': sender_address,
                'gas': gas_estimate,
                'gasPrice': gas_price,
                'nonce': nonce
            })
            signed_txn = web3_instance.eth.account.sign_transaction(transaction, private_key=private_key)
            tx_hash = web3_instance.eth.send_raw_transaction(signed_txn.rawTransaction)
            tx_receipt = web3_instance.eth.wait_for_transaction_receipt(tx_hash)
            logging.info("地址 %s 交易成功，交易哈希: %s", sender_address, web3_instance.to_hex(tx_hash))
        except Exception as e:
            logging.error("处理地址 %s 的交易时发生错误: %s", sender_address, str(e))

    def run(self):
        logging.info("程序启动，开始执行奖励领取操作...")
        while True:
            try:
                accounts_data = self.load_accounts_data()
                for acc in accounts_data:
                    web3_instance = self.setup_blockchain_connection(acc.get('proxy'))
                    if not web3_instance:
                        logging.error("连接失败，跳过当前账号...")
                        continue

                    contract_instance = web3_instance.eth.contract(
                        address=Web3.to_checksum_address(self.contract_address),
                        abi=self.contract_abi
                    )
                    self.claim_rewards(acc['private_key'], web3_instance, contract_instance)
                logging.info("%s 本轮领取完成，等待6小时后继续运行...", self.current_time())
                time.sleep(6 * 60 * 60)
            except KeyboardInterrupt:
                logging.warning("程序已停止运行")
                sys.exit(0)
            except Exception as e:
                logging.error("发生错误: %s", str(e))
                time.sleep(60)

if __name__ == "__main__":
    bot = HumanityProtocolBot()
    bot.run()
