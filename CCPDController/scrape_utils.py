import re
import time
import random

from bs4 import BeautifulSoup

from CCPDController.web_driver import create_driver

# Amazon scrapping utils 
# works for both Amazon CA nad US

# parent of cols
parentId = 'ppd'

# center col div id and class name
centerColId = 'centerCol'
centerColClass = 'centerColAlign'

# right col tag id and class name
rightColId = 'rightCol'
rightColClass = 'rightCol'

# product title span id
productTitleId = 'productTitle'

# price tag class
priceWholeClass = 'a-price-whole'
priceFractionClass = 'a-price-fraction'
priceRangeClass = 'a-price-range'

# image container class
imageContainerClass = 'imgTagWrapper'

# random wait time between scrape
random_wait = lambda : time.sleep(random.randint(30, 50))

# extract url from string using regex
def extract_urls(input):
    try:
        words = input.split()
        regex = re.compile(r'https?://\S+')
        return [word for word in words if regex.match(word)][0]
    except:
        # Search for the URL in the string
        match = re.search(r'https?://\S+', input)
        if match:
            found_url = match.group()
            return found_url
        else:
            return 'No URL Found'
            
# returns children of top nav bar/belt
def getNavBelt(response):
    children = response.xpath(f'//div[@class="nav-right"]/child::*')
    if len(children) < 1:
        raise Exception('No right nav belt found')
    return children

# takes scrapy HtmlResponse generated from rawHTML
# return array of center col's children tags
def getCenterCol(response):
    children = response.xpath(f'//div[@id="{centerColId}" or @class="{centerColClass}"]/child::*')
    
    # if no children, retarget parent
    if len(children) < 1:
        parent = response.xpath(f'//div[@id="{parentId}"]/child::*')
        children = parent.xpath(f'//div[@id="{centerColId}" or @class="{centerColClass}"]/child::*')
            
    # return error if no center col found
    if len(children) < 1:
        raise Exception('No center column found')
    return children

def getRightCol(response):
    # id = "unqualifiedBuyBox"
    children = response.xpath(f'//div[@id="{rightColId}" or @class="{rightColClass}"]/child::*')
    if len(children) < 1:
        raise Exception('No right column found')
    return children

# takes scrapy HtmlResponse object and returns title
def getTitle(response) -> str:
    arr = getCenterCol(response)
    
    # get title
    # remove whitespace around it
    for p in arr.xpath(f'//span[@id="{productTitleId}"]/text()'):
        title = p.extract().strip()
    return title

# takes rawHTML str and returns:
# - msrp in float 
# - msrp range in array of float
# - or price unavailable string
def getMsrp(response):
    center = getCenterCol(response)
    right = getRightCol(response)
    
    
    # check for out of stock id in right col
    outOfStock = right.xpath('//div[@id="outOfStock"]').getall()
    if len(outOfStock) > 1:
        return 'Currently unavailable'
    
    # if 'unqualifiedBuyBox' appears in arr for more than 2 times, return unavailable
    unqualifiedBox = right.xpath('//div[@id="unqualifiedBuyBox"]').getall()[:4]
    if len(unqualifiedBox) > 2:
        return 'Currently unavailable'
    
    # Currently unavailable in right col set

    # grab price in span tag 
    # msrp whole joint by fraction
    integer = center.xpath(f'//span[has-class("{priceWholeClass}")]/text()').extract()
    decimal = center.xpath(f'//span[has-class("{priceFractionClass}")]/text()').extract()
    if integer and decimal:
        price = float(integer[0] + '.' + decimal[0])
        return price
    
    # extract price range if no fixed price
    price = []
    rangeTag = center.xpath(f'//span[@class="{priceRangeClass}"]/child::*')
    for p in rangeTag.xpath('//span[@data-a-color="price" or @class="a-offscreen"]/text()').extract():
        if '$' in p and p not in price:
            price.append(p)
    return price

# takes scrapy response and get full quality stock image
# src is the low quality image
# Amazon CA
def getImageUrl(response):
    # the image will be inside this div id="imgTagWrapperId" class="imgTagWrapper"
    # gets the img tag
    img = response.xpath(f'//div[@class="{imageContainerClass}"]/child::*').extract_first()
    # src = img.xpath('//img/@src').get()
    
    # use bs4 to extract src attribute of that img tag
    soup = BeautifulSoup(img, 'html.parser')
    src = soup.find('img')['src']
    return src
    # if img:
    #     http_pattern = re.compile(r'https?://\S+')
    #     res = http_pattern.findall(img)
    #     # return res[:2] # for both lq and hq image
    #     # slice the last 1 char (/) in string or it will give bad request
    #     return res[1][:-1]

# look for US and CA flag
# <i class="icp-flyout-flag icp-flyout-flag-ca"></i>
# <span class="icp-nav-flag icp-nav-flag-ca icp-nav-flag-lop"></span>

# US flag class: 
# icp-flyout-flag-us
# icp-nav-flag-us
# flag-us

# CA flag class: 
# icp-flyout-flag-ca
# icp-nav-flag-ca
# flag-ca

# UK flag class: 
# icp-flyout-flag-gb
# icp-nav-flag-gb
# flag-gb

def getCurrency(response) -> str:
    # grab nav belt
    nav = getNavBelt(response)
    
    # init indicator to 0
    us_exist = 0
    ca_exist = 0
    gb_exist = 0
    
    # grab nav-tools div
    for e in nav.xpath(f'//div[@id="nav-tools"]/child::*').getall():
        if len(re.findall('flag-us', e)) > 0:
            us_exist = 1
        if len(re.findall('flag-ca', e)) > 0:
            ca_exist = 1
        if len(re.findall('flag-gb', e)) > 0:
            gb_exist = 1

    if us_exist > 0:
        return 'USD'
    if ca_exist > 0:
        return 'CAD'
    if gb_exist > 0:
        return 'GBP'
    return 'No currency info'


def webDriverGet(url: str) -> str:
    # Use the WebDriver to navigate to a webpage
    driver = create_driver()
    driver.get(url)
    
    # Get and print the title of the page
    print(f"Page title is: {driver.title}")
    
    # Always remember to quit the driver
    driver.quit()
    return ''