from django.urls import path
from . import views

# define all routes
urlpatterns = [
    # QA inventory
    path("getInventoryBySku", views.getInventoryBySku, name="getInventoryBySku"),
    path("createInventory", views.createInventory, name="createInventory"),
    path("deleteInventoryBySku", views.deleteInventoryBySku, name="deleteInventoryBySku"), 
    path("updateInventoryBySku/<str:sku>", views.updateInventoryBySku, name="updateInventoryBySku"),
    path("getInventoryByOwnerId/<int:page>", views.getInventoryByOwnerId, name="getInventoryByOwnerId"),
    path("getInventoryInfoByOwnerId", views.getInventoryInfoByOwnerId, name="getInventoryInfoByOwnerId"),
    path("getInventoryByOwnerName", views.getInventoryByOwnerName, name="getInventoryByOwnerName"),
    path("getQAInfoByOwnerName", views.getQAInfoByOwnerName, name="getQAInfoByOwnerName"),
    path("getAllQAShelfLocations", views.getAllQAShelfLocations, name="getAllQAShelfLocations"),
    path("getDailyQARecordData", views.getDailyQARecordData, name="getDailyQARecordData"),
    path("getShelfSheetByUser", views.getShelfSheetByUser, name="getShelfSheetByUser"),
    path("getAllShelfSheet", views.getAllShelfSheet, name="getAllShelfSheet"),
    # instock inventory
    path("getInstockByPage", views.getInstockByPage, name="getInstockByPage"),
    path("getInstockBySku", views.getInstockBySku, name="getInstockBySku"),
    path("getAllShelfLocations", views.getAllShelfLocations, name="getAllShelfLocations"),
    path("createInstockInventory", views.createInstockInventory, name="createInstockInventory"),
    path("updateInstockBySku", views.updateInstockBySku, name="updateInstockBySku"),
    path("deleteInstockBySku", views.deleteInstockBySku, name="deleteInstockBySku"),
    path("getAuctionCsv", views.getAuctionCsv, name="getAuctionCsv"),
    path("getAbnormalInstockInventory", views.getAbnormalInstockInventory, name="getAbnormalInstockInventory"),
    path("restock", views.restock, name="restock"),
    # auction function
    path("getAuctionRemainingRecord", views.getAuctionRemainingRecord, name="getAuctionRemainingRecord"),
    path("createAuctionRecord", views.createAuctionRecord, name="createAuctionRecord"),
    path("createRemainingRecord", views.createRemainingRecord, name="createRemainingRecord"),
    path("updateRemainingToDB", views.updateRemainingToDB, name="updateRemainingToDB"),
    path("addTopRowItem", views.addTopRowItem, name="addTopRowItem"),
    path("deleteTopRowItem", views.deleteTopRowItem, name="deleteTopRowItem"),
    path("deleteAuctionRecord", views.deleteAuctionRecord, name="deleteAuctionRecord"),
    path("deleteRemainingRecord", views.deleteRemainingRecord, name="deleteRemainingRecord"),
    path("getRemainingLotNumbers", views.getRemainingLotNumbers, name="getRemainingLotNumbers"),
    path("importUnsoldItems", views.importUnsoldItems, name="importUnsoldItems"),
    path("deleteUnsoldItems", views.deleteUnsoldItems, name="deleteUnsoldItems"),
    path("auditRemainingRecord", views.auditRemainingRecord, name="auditRemainingRecord"),
    path("deleteItemInAuction", views.deleteItemInAuction, name="deleteItemInAuction"),
    path("updateItemInAuction", views.updateItemInAuction, name="updateItemInAuction"),
    path("addSelectionToAuction", views.addSelectionToAuction, name="addSelectionToAuction"),
    # scraping function
    path("generateDescriptionBySku", views.generateDescriptionBySku, name="generateDescriptionBySku"),
    path("scrapeInfoBySkuAmazon", views.scrapeInfoBySkuAmazon, name="scrapeInfoBySkuAmazon"),
    path("scrapePriceBySkuHomeDepot", views.scrapePriceBySkuHomeDepot, name="scrapePriceBySkuHomeDepot"),
    path("sendInstockCSV", views.sendInstockCSV, name="sendInstockCSV"),
    path("sendQACSV", views.sendQACSV, name="sendQACSV"),
    path("fillPlatform", views.fillPlatform, name="fillPlatform"),
]
