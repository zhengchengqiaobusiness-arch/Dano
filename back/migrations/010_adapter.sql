-- M0:代码适配器(goal 模式自动生成的可执行 Skill)。
--   1) assets.asset_type 纳入 'adapter';
--   2) validation_runs.kind 增 'vuln'(漏洞校验证据,M2 起作为发布必需种类之一)。
-- 幂等:每次启动都跑 migrations,故 DROP IF EXISTS 后再 ADD。

ALTER TABLE assets DROP CONSTRAINT IF EXISTS assets_asset_type_check;
ALTER TABLE assets ADD CONSTRAINT assets_asset_type_check
    CHECK (asset_type IN ('field_mapping','connector','policy_rule','env_profile','page_script','workflow','adapter'));

ALTER TABLE validation_runs DROP CONSTRAINT IF EXISTS validation_runs_kind_check;
ALTER TABLE validation_runs ADD CONSTRAINT validation_runs_kind_check
    CHECK (kind IN ('connect','sandbox','readback','health','replay','cases','vuln'));
