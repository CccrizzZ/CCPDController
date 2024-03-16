from pyexpat import model
from django.db import models

CONDITION_CHOISES = [
    ('New', 'New'),
    ('Sealed', 'Sealed'),
    ('Used', 'Used'),
    ('Used Like New', 'Used Like New'),
    ('Damaged', 'Damaged'),
    ('As Is', 'As Is'),
]

PLATFORM_CHOISES = [
    ('Amazon', 'Amazon'),
    ('eBay', 'eBay'),
    ('HomeDepot', 'HomeDepot'),
    ('AliExpress', 'AliExpress'),
    ('Official Website', 'Official Website'),
    ('BestBuy', 'BestBuy'),
    ('Walmart', 'Walmart'),
    ('HomeDepot', 'HomeDepot'),
    ('Facebook', 'Facebook'),
    ('Kijiji', 'Kijiji'),
    ('Other', 'Other')
]

MARKETPLACE_CHOISES = [
    ('Hibid', 'Hibid'),
    ('Retail', 'Retail'),
    ('eBay', 'eBay'),
    ('Wholesale', 'Wholesale'),
    ('Other', 'Other')
]

class InventoryItem(models.Model): 
    _id = models.AutoField(primary_key=True)
    time = models.CharField(max_length=30)
    sku = models.IntegerField()
    itemCondition = models.CharField(max_length=14, choices=CONDITION_CHOISES)
    comment = models.TextField(max_length=100)
    link = models.TextField(max_length=300)
    platform = models.CharField(max_length=17, choices=PLATFORM_CHOISES)
    shelfLocation = models.CharField(max_length=4)
    amount = models.IntegerField()
    owner = models.CharField(max_length=32)
    ownerName = models.CharField(max_length=40)
    marketplace = models.CharField(max_length=10, choices=MARKETPLACE_CHOISES)
    
    # constructor input all info
    def __init__(self, time, sku, itemCondition, comment, link, platform, shelfLocation, amount, owner, ownerName, marketplace) -> None:
        self.time = time
        self.sku = sku
        self.itemCondition=itemCondition
        self.comment = comment
        self.link = link
        self.platform = platform
        self.shelfLocation = shelfLocation
        self.amount = amount
        self.owner = owner
        self.ownerName = ownerName
        self.marketplace = marketplace
        
    # return inventory sku
    def __str__(self) -> str:
        return str(self.sku)

class InstockInventory(models.Model):
    sku = models.IntegerField()
    time = models.CharField(max_length=30)
    shelfLocation = models.CharField(max_length=4)
    condition = models.CharField(max_length=14, choices=CONDITION_CHOISES)
    comment = models.TextField(max_length=100)
    lead = models.TextField(max_length=100)
    description = models.TextField(max_length=260)
    url = models.TextField(max_length=300)
    marketplace = models.CharField(max_length=10, choices=MARKETPLACE_CHOISES)
    platform = models.CharField(max_length=17, choices=PLATFORM_CHOISES)
    adminName = models.CharField(max_length=40)
    qaName = models.CharField(max_length=40)
    quantityInstock:int = models.IntegerField()
    quantitySold:int = models.IntegerField()
    msrp = models.FloatField()

    def __init__(self, sku, time, shelfLocation, condition, comment, lead, description, url, marketplace, platform, adminName, qaName, quantityInstock, quantitySold, msrp) -> None:
        self.sku = sku
        self.time = time
        self.shelfLocation = shelfLocation
        self.condition = condition
        self.comment = comment
        self.lead = lead
        self.description = description
        self.url = url
        self.marketplace = marketplace
        self.platform = platform
        self.adminName = adminName
        self.qaName = qaName
        self.quantityInstock = quantityInstock
        self.quantitySold = quantitySold
        self.msrp = msrp
        
    # return inventory sku
    def __str__(self) -> str:
        return str(self.sku)

class AuctionItem(models.Model):
    lot = models.IntegerField # item lot number inside auction
    lead = models.CharField(max_length=100)
    description = models.CharField(max_length=300)
    msrp = models.IntegerField()
    shelfLocation = models.CharField(max_length=6)
    sku = models.IntegerField()
    startBid = models.FloatField()
    reserve = models.FloatField()

    def __init__(self, lot, lead, description, msrp, shelfLocation, sku, startBid, reserve) -> None:
        self.lot = lot
        self.lead = lead
        self.description = description
        self.msrp = msrp
        self.shelfLocation = shelfLocation
        self.sku = sku
        self.startBid = startBid
        self.reserve = reserve
    
    # return inventory sku
    def __str__(self) -> str:
        return str(self.sku)

class AuctionRecord(models.Model): 
    lot: int = models.IntegerField()
    totalItems: int = models.IntegerField()
    openTime: str = models.CharField(max_length=30)
    closeTime: str = models.CharField(max_length=30)
    closed: bool = models.BooleanField()
    title: str = models.CharField(max_length=100)
    description: str = models.CharField(max_length=300)
    minMSRP: float = models.FloatField()
    maxMSRP: float = models.FloatField()
    remainingResolved: str = models.BooleanField()
    minSku: int = models.IntegerField()
    maxSku: int = models.IntegerField()

    # excluded:
    # itemsArr: InstockItem[],
    # topRow: InstockItem[],

    def __init__(self, lot, totalItems, openTime, closeTime, closed, title, description, minMSRP, maxMSRP, remainingResolved, minSku, maxSku) -> None:
        self.lot = lot
        self.totalItems = totalItems
        self.openTime = openTime
        self.closeTime = closeTime
        self.closed = closed
        self.title = title
        self.description = description
        self.minMSRP = minMSRP
        self.maxMSRP = maxMSRP
        self.remainingResolved = remainingResolved
        self.minSku = minSku
        self.maxSku = maxSku

    # return inventory sku
    def __str__(self) -> str:
        return str(self.lot)