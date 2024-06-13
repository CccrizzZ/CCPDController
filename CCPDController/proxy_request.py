import os
import requests
import random
import time
from fake_useragent import UserAgent
ua = UserAgent()

# these are the proxies from 
proxies_list = [
    '38.154.227.167:5868',
    '185.199.229.156:7492',
    '185.199.228.220:7300',
    '185.199.231.45:8382',
    '188.74.210.207:6286',
    '188.74.183.10:8279',
    '188.74.210.21:6100',
    '45.155.68.129:8133',
    '154.95.36.199:6893',
    '45.94.47.66:8110',
]

# generate header with random user agent
headers = {
    'User-Agent': f'user-agent={ua.random}',
    'Accept-Language': 'en-US,en;q=0.9',
}

def request_with_proxy(url):
    # select random proxy from the list
    proxy_ip = random.choice(proxies_list)
    proxy = f"http://{os.getenv('WEBSHARE_USERNAME')}:{os.getenv('WEBSHARE_PASSWORD')}@{proxy_ip}"
    proxies = {
        "http": proxy,
        "https": proxy
    }
    
    # wait random milliseconds before request
    delay = random.uniform(1, 3)
    time.sleep(delay)
    # send the request
    response = requests.get(
        url, 
        proxies=proxies, 
        timeout=10, 
        headers=headers
    )
    return response
