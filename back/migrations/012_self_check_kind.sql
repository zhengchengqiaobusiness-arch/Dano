-- 流程:录制抓请求页面发布前的**确定性结构自检**(self_check)证据 —— P0 承重闸门。
-- validation_runs.kind 增 'self_check':录制 V2 对 capture 页面记此证据,verify_publishable 要求 self_check 覆盖。
ALTER TABLE validation_runs DROP CONSTRAINT IF EXISTS validation_runs_kind_check;
ALTER TABLE validation_runs ADD CONSTRAINT validation_runs_kind_check
    CHECK (kind IN ('connect','sandbox','readback','health','cases','self_check'));
