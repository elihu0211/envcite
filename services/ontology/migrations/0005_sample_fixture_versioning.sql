-- 0005_sample_fixture_versioning — bring the placeholder fixture into the shape
-- 0004 requires. **No new data**: this only attaches the rows 0002 already created
-- to a version, because 0002 is applied and immutable (its checksum is recorded).
--
-- Still not authoritative legal data. The 施行 dates below are invented.

UPDATE legal_expression
   SET valid_from    = DATE '2000-01-03',   -- effective_from_publication('2000-01-01')
       version_label = '樣本版本'
 WHERE resource_id = 'air-pollution-control-act';

UPDATE legal_expression
   SET version_label = '樣本版本'
 WHERE resource_id = 'water-pollution-control-act';

-- Attach the existing articles to the only text their law has.
UPDATE subdivision AS s
   SET expression_iri = e.iri
  FROM legal_expression AS e
 WHERE e.resource_id = s.resource_id
   AND s.expression_iri IS NULL;
