-- 0003_suspension_enum — 恢復適用事件與停止適用中的條文狀態
--
-- 中央法規標準法第 19 條：法規得暫停適用其一部或全部；停止或恢復適用之程序準用廢止或制定之規定。
-- 停止適用已是事件種類，恢復適用不是；停止適用中的條文有文字與現行版本，但不是有效規範，
-- 不能歸入 in_force，也不是 no_longer_in_force（會恢復）。新值要先提交才能使用，所以單獨一檔，
-- 使用它們的約束與 corpus.provisions_at 在 0003_suspension_status。

ALTER TYPE corpus.event_kind ADD VALUE 'resumption' AFTER 'suspension';
ALTER TYPE corpus.provision_status ADD VALUE 'suspended' AFTER 'no_longer_in_force';
