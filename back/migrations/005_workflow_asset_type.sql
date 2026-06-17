-- 放开 assets.asset_type 检查约束,纳入 workflow(复合流程 Skill,阶段2)。
-- 幂等:每次启动都跑 migrations,故 DROP IF EXISTS 后再 ADD。
ALTER TABLE assets DROP CONSTRAINT IF EXISTS assets_asset_type_check;
ALTER TABLE assets ADD CONSTRAINT assets_asset_type_check
    CHECK (asset_type IN ('field_mapping','connector','policy_rule','env_profile','page_script','workflow'));
