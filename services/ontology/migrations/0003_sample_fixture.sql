-- 0003_sample_fixture — 樣本：兩部法律的現行版本與數條條文
--
-- 文字取自全國法規資料庫 2026-09-18 資料（政府資料開放授權條款第 1 版，須標示出處），
-- 地位標為官方資料庫。只是樣本，不代表語料：真實資料由匯入管線寫入。
-- 兩部法律皆「自公布日施行」，依中央法規標準法第 13 條自公布日起算至第三日生效。

INSERT INTO corpus.legal_resource (id_scheme, local_id, kind, document_type) VALUES
    ('moj_pcode', 'O0020001', 'statute', '法律'),
    ('moj_pcode', 'O0040001', 'statute', '法律');

INSERT INTO corpus.legal_expression (resource_id, language, version_date, title, text_period, text_status, authority)
SELECT r.id, 'zho', v.version_date, v.title, daterange(v.version_date, NULL), 'available', 'official_db'
  FROM (VALUES
          ('O0020001', DATE '2018-08-01', '空氣污染防制法'),
          ('O0040001', DATE '2018-06-13', '水污染防治法')
       ) AS v (local_id, version_date, title)
  JOIN corpus.legal_resource AS r ON r.id_scheme = 'moj_pcode' AND r.local_id = v.local_id;

INSERT INTO corpus.lifecycle_event (resource_id, kind, event_date, document_no, expression_id)
SELECT r.id, v.kind::corpus.event_kind, v.event_date, v.document_no, e.id
  FROM (VALUES
          ('O0020001', 'enactment',         DATE '1975-05-23', '（64）台統（一）義字第 2265 號', NULL),
          ('O0020001', 'full_amendment',    DATE '2018-08-01', '華總一義字第 10700080891 號', DATE '2018-08-01'),
          ('O0040001', 'enactment',         DATE '1974-07-11', '（63）台統（一）義字第 3040 號', NULL),
          ('O0040001', 'partial_amendment', DATE '2018-06-13', NULL, DATE '2018-06-13')
       ) AS v (local_id, kind, event_date, document_no, version_date)
  JOIN corpus.legal_resource AS r ON r.id_scheme = 'moj_pcode' AND r.local_id = v.local_id
  LEFT JOIN corpus.legal_expression AS e ON e.resource_id = r.id AND e.version_date = v.version_date;

INSERT INTO corpus.provision (resource_id, provision_type, path, sort_key)
SELECT r.id, 'article', v.path, v.sort_key
  FROM (VALUES
          ('O0020001', 'art_1',   ARRAY[1]),
          ('O0020001', 'art_100', ARRAY[100]),
          ('O0040001', 'art_7',   ARRAY[7]),
          ('O0040001', 'art_40',  ARRAY[40])
       ) AS v (local_id, path, sort_key)
  JOIN corpus.legal_resource AS r ON r.id_scheme = 'moj_pcode' AND r.local_id = v.local_id;

INSERT INTO corpus.provision (resource_id, parent_id, provision_type, path, sort_key)
SELECT a.resource_id, a.id, 'paragraph', a.path || '/para_' || v.n, a.sort_key || 0 || v.n
  FROM corpus.provision AS a
  JOIN corpus.legal_resource AS r ON r.id = a.resource_id AND r.local_id = 'O0040001'
 CROSS JOIN (VALUES (1), (2)) AS v (n)
 WHERE a.provision_type = 'article';

INSERT INTO corpus.provision_version (provision_id, expression_id, resource_id, content, in_force)
SELECT p.id, e.id, p.resource_id, v.content, daterange(e.version_date + 2, NULL)
  FROM (VALUES
          ('O0020001', 'art_1',
           '為防制空氣污染，維護生活環境及國民健康，以提高生活品質，特制定本法。'),
          ('O0020001', 'art_100', '本法自公布日施行。'),
          ('O0040001', 'art_7', NULL),
          ('O0040001', 'art_7/para_1',
           '事業、污水下水道系統或建築物污水處理設施，排放廢（污）水於地面水體者，應符合放流水標準。'),
          ('O0040001', 'art_7/para_2',
           '前項放流水標準，由中央主管機關會商相關目的事業主管機關定之，其內容應包括適用範圍、管制方式、項目、濃度或總量限值、研訂基準及其他應遵行之事項。直轄市、縣（市）主管機關得視轄區內環境特殊或需特予保護之水體，就排放總量或濃度、管制項目或方式，增訂或加嚴轄內之放流水標準，報請中央主管機關會商相關目的事業主管機關後核定之。'),
          ('O0040001', 'art_40', NULL),
          ('O0040001', 'art_40/para_1',
           '事業或污水下水道系統排放廢（污）水，違反第七條第一項或第八條規定者，處新臺幣六萬元以上二千萬元以下罰鍰，並通知限期改善，屆期仍未完成改善者，按次處罰；情節重大者，得令其停工或停業；必要時，並得廢止其水污染防治許可證（文件）或勒令歇業。'),
          ('O0040001', 'art_40/para_2',
           '畜牧業違反第七條第一項或第八條之規定者，處新臺幣六千元以上六十萬元以下罰鍰，並通知限期改善，屆期仍未完成改善者，按次處罰；情節重大者，得令其停工或停業；必要時，並得廢止其水污染防治許可證（文件）或勒令歇業。')
       ) AS v (local_id, path, content)
  JOIN corpus.legal_resource AS r ON r.id_scheme = 'moj_pcode' AND r.local_id = v.local_id
  JOIN corpus.provision AS p ON p.resource_id = r.id AND p.path = v.path
  JOIN corpus.legal_expression AS e ON e.resource_id = r.id;
