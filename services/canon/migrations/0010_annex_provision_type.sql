-- 0010_annex_provision_type — 條文單元加一種：附表
--
-- 排放標準的限值寫在附表（放流水標準的化學需氧量限值在附表三），條文只說「適用附表三」。附表要讓標註管線
-- 看得到，就必須是語料裡的單元。新增的列舉值要先提交，下一支 migration 才能用它。
ALTER TYPE corpus.provision_type ADD VALUE 'annex';
