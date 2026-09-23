-- 0002_sample_fixture — 佔位樣本，**不是**權威法條資料。
--
-- 條文清單與日期皆為占位；法規名稱雖為真實中文法規名，但本檔不得作為法源引用。
-- 權威來源為全國法規資料庫（政府資料開放授權條款－第1版，需標示出處），其匯入
-- 管線尚未建立（見 env-law-docs 的 09-01 未竟之處其二）。

INSERT INTO legal_resource (id_local, iri, date_document) VALUES
  ('air-pollution-control-act',   'https://env.law/eli/tw/law/air-pollution-control-act',   DATE '2000-01-01'),
  ('water-pollution-control-act', 'https://env.law/eli/tw/law/water-pollution-control-act', NULL);

INSERT INTO legal_expression (iri, resource_id, title, language) VALUES
  ('https://env.law/eli/tw/law/air-pollution-control-act/zho',   'air-pollution-control-act',   '空氣污染防制法', 'zh-hant'),
  ('https://env.law/eli/tw/law/water-pollution-control-act/zho', 'water-pollution-control-act', '水污染防治法',   'zh-hant');

INSERT INTO subdivision (iri, resource_id, number) VALUES
  ('https://env.law/eli/tw/law/air-pollution-control-act/art_1',   'air-pollution-control-act', '1'),
  ('https://env.law/eli/tw/law/air-pollution-control-act/art_2',   'air-pollution-control-act', '2'),
  ('https://env.law/eli/tw/law/air-pollution-control-act/art_5-1', 'air-pollution-control-act', '5-1'),
  ('https://env.law/eli/tw/law/air-pollution-control-act/art_10',  'air-pollution-control-act', '10');
