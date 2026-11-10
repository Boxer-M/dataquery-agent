-- Explicit synthetic integration fixture. Never used as production or original business data.
INSERT INTO ods.categories VALUES (1,'fixture root',NULL),(2,'fixture child',1);
INSERT INTO ods.customers VALUES
 (1,'Fixture','One','same@example.test','fixture city','ZZ','2020-12-31',NULL,false),
 (2,'Fixture','Two','same@example.test','fixture city','ZZ','2021-01-01','gold',true),
 (3,'Fixture','Three','third@example.test','fixture city','ZZ','2024-02-29',NULL,false);
INSERT INTO ods.products VALUES
 (1,'fixture-1','fixture product',2,99.00,30.00,NULL),
 (2,'fixture-2','fixture product',2,4.00,1.00,'2025-01-15');
INSERT INTO ods.orders VALUES
 (1,1,'2025-01-01','2025-01-02','delivered','web','ZZ',NULL),
 (2,1,'2025-01-31 23:59:59',NULL,'cancelled','mobile','ZZ',NULL),
 (3,2,'2025-02-01','2025-02-03','delivered','web','ZZ','fixture'),
 (4,3,'2025-01-05',NULL,'processing','web','ZZ',NULL);
INSERT INTO ods.order_items VALUES
 (11,1,1,2,10.10,0.20),(12,1,2,1,3,0),(13,2,2,1,4,0),
 (14,3,1,2,5,12),(15,3,2,0,1,0),(16,3,1,1,-3,-1);
INSERT INTO ods.payments VALUES
 (101,1,'card',23,'2025-01-01','failed','fixture decline'),
 (102,1,'card',23,'2025-01-02','captured',NULL),
 (103,2,'card',4,'2025-01-31','failed','fixture decline'),
 (104,2,'card',4,'2025-02-01','captured',NULL),
 (105,3,'card',0,'2025-02-01','captured',NULL),
 (106,4,'card',-1,'2025-01-05','captured',NULL);
INSERT INTO ods.returns VALUES
 (201,11,'2024-12-31','fixture reason',5,false),
 (202,11,'2025-02-02','fixture reason',30,true),
 (203,13,'2025-02-01','fixture reason',4,false),
 (204,14,'2025-02-02','fixture reason',-1,false);
