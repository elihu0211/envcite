-- 0069_canon_producers — 規則產生的標註，生產者名稱以服務名 canon 為前綴
--
-- derivation.producer 是出處標籤（誰產生了這批標註），程式只寫入、不依名稱比對既有列，
-- 所以舊標籤改名不影響行為；改掉是為了讓資料庫裡不留舊的專案名。

UPDATE annotation.derivation SET producer = 'canon-delegation-rule'
 WHERE producer = 'envcite-delegation-rule';
UPDATE annotation.derivation SET producer = 'canon-guidance-sync'
 WHERE producer = 'envcite-guidance-sync';
