import asyncio
import os
from scrapy.http import HtmlResponse
import aiohttp
import requests
import random
from random import choice
from fake_useragent import UserAgent
ua = UserAgent()


# these are the proxies from 
proxies_list = [
    '216.173.76.195:6822',
    '104.224.90.189:6350',
    '154.29.233.143:5904',
    '104.239.39.7:5936',
    '45.135.139.3:6306',
    '45.141.80.118:5844',
    '107.181.141.242:6639',
    '173.0.9.37:5620',
    '104.143.226.117:5720',
    '104.223.149.202:5830',
    '89.116.77.193:6188',
    '45.61.122.182:6474',
    '134.73.94.9:5979',
    '104.223.157.62:6301',
    '45.43.83.232:6515',
    '45.141.80.162:5888',
    '104.143.224.70:5931',
    '204.217.245.126:6717',
    '45.141.80.68:5794',
    '84.46.204.195:6498',
    '38.153.134.82:9751',
    '198.46.148.94:5782',
    '104.239.6.147:6281',
    '216.10.27.206:6884',
    '77.83.233.25:6643',
    '155.254.48.139:6045',
    '45.146.31.187:5774',
    '104.239.80.202:5780',
    '104.239.38.116:6649',
    '38.153.134.184:9853',
    '45.43.70.183:6470',
    '173.214.176.7:5978',
    '173.211.8.154:6266',
    '166.88.58.8:5733',
    '184.174.56.153:5165',
    '206.41.179.112:5788',
    '107.181.142.111:5704',
    '107.181.141.81:6478',
    '107.181.152.52:5089',
    '104.239.40.224:6843',
    '198.105.111.193:6871',
    '107.181.154.42:5720',
    '104.239.42.251:6276',
    '45.41.171.195:6231',
    '104.239.38.208:6741',
    '104.239.35.0:5682',
    '216.173.99.147:6489',
    '45.151.163.95:5848',
    '45.41.173.67:6434',
    '172.98.168.186:6833',
    '107.181.148.43:5903',
    '104.250.204.89:6180',
    '216.173.104.147:6284',
    '104.239.37.197:5849',
    '198.105.100.150:6401',
    '209.99.165.72:5977',
    '104.239.42.66:6091',
    '104.239.37.47:5699',
    '45.41.169.145:6806',
    '217.69.127.107:6728',
    '45.146.31.4:5591',
    '91.223.126.99:6711',
    '216.173.107.152:6120',
    '45.41.171.10:6046',
    '185.48.52.248:5840',
    '45.41.171.141:6177',
    '104.239.13.211:6840',
    '64.137.95.76:6559',
    '45.146.30.217:6721',
    '45.117.55.76:6722',
    '206.41.174.77:6032',
    '206.41.169.209:5789',
    '104.239.37.245:5897',
    '64.137.95.215:6698',
    '194.113.112.240:6135',
    '216.173.99.14:6356',
    '104.233.19.51:5723',
    '216.173.98.65:6067',
    '194.113.119.68:6742',
    '185.48.55.39:6515',
    '104.239.10.222:5893',
    '64.137.10.85:5735',
    '104.233.16.226:6490',
    '216.173.105.36:5893',
    '216.173.105.215:6072',
    '216.173.104.49:6186',
    '216.173.104.232:6369',
    '185.48.52.181:5773',
    '185.48.55.31:6507',
    '185.48.55.227:6703',
    '216.173.105.216:6073',
    '216.173.105.76:5933',
    '216.173.104.210:6347',
    '216.173.105.113:5970',
    '216.173.105.206:6063',
    '185.48.55.179:6655',
    '216.173.104.23:6160',
    '185.48.52.68:5660',
    '216.173.104.4:6141',
    '185.48.52.204:5796',
]

accepted_language = [
    'en-US,en;q=1'
    'en-US,en;q=0.9',
    'en-US,en;q=0.8'
    'en-US,en;q=0.7',
    '*;q=0.7'
    '*;q=0.8'
]

# generate header with random user agent
def getRandomHeader():
    agent = ua.random
    return {
        'User-Agent': f'user-agent={agent}',
        'Accept-Language':  random.choice(accepted_language),
    }

# srape url with 1 proxy
def request_with_proxy_admin(url):
    # select random proxy from the list
    proxy = f"http://{os.getenv('WEBSHARE_USERNAME')}:{os.getenv('WEBSHARE_PASSWORD')}@{choice(proxies_list)}"
    proxies = {
        "http": proxy,
        "https": proxy
    }
    
    # send the request
    response = requests.get(
        url, 
        proxies=proxies, 
        timeout=10, 
        headers=getRandomHeader()
    )
    http_res = HtmlResponse(url=url, body=response.text, encoding='utf-8')
    return http_res


# srape url with 1 proxy
async def request_with_proxy(url):
    # select random proxy from the list
    proxy = f"http://{os.getenv('WEBSHARE_USERNAME')}:{os.getenv('WEBSHARE_PASSWORD')}@{choice(proxies_list)}"
    proxies = {
        "http": proxy,
        "https": proxy
    }
    
    # send the request
    response = requests.get(
        url, 
        proxies=proxies, 
        timeout=10, 
        headers=getRandomHeader()
    )
    http_res = HtmlResponse(url=url, body=response.text, encoding='utf-8')
    return http_res

# srape url with 1 proxy
async def request_with_proxy_aio(session: aiohttp.ClientSession, url: str, proxy: str):
    try:
        proxy = f"http://{os.getenv('WEBSHARE_USERNAME')}:{os.getenv('WEBSHARE_PASSWORD')}@{choice(proxies_list)}"
        async with session.get(url, proxy=proxy, timeout=10, headers=getRandomHeader()) as res:
            if res.status == 200:
                data = await res.text
                return data
            else:
                return None
    except:
        return None

# parallel srape url with 3 proxy
async def parallelRequest(url):
    async with aiohttp.ClientSession() as session:
        # populate task
        tasks = []
        for _ in range(3):
            task = asyncio.create_task(request_with_proxy(url))
            tasks.append(task)
        
        # execute task parallel
        responsesArr = await asyncio.gather(*tasks)
        for res in responsesArr:
            if res is not None:
                return res