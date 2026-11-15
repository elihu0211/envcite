-- 0007_provision_version_by_provision — 外鍵刪除連鎖所需的索引
--
-- 0006 以部分唯一索引與排除約束取代了整表的（單元，版本）唯一約束，而它們都不能服務
-- 「依單元找版本」的查詢：刪除一個條文單元時，連鎖刪除其版本的外鍵檢查退為全表掃描，
-- 一部法規的重寫因此從秒級變成每部約十秒（全量檔 91 萬列）。補一個一般索引。
CREATE INDEX provision_version_by_provision ON corpus.provision_version (provision_id);
