-- 流程4:制度规则用例验证证据。validation_runs.kind 增 'cases'(用例全通过才可发布制度规则)。
-- 幂等:先 DROP 旧 CHECK 再 ADD 含 'cases' 的新 CHECK。

ALTER TABLE validation_runs DROP CONSTRAINT IF EXISTS validation_runs_kind_check;
ALTER TABLE validation_runs ADD CONSTRAINT validation_runs_kind_check
    CHECK (kind IN ('connect','sandbox','readback','health','replay','cases'));
