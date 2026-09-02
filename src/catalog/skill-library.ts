import path from "node:path";
import { rename, stat } from "node:fs/promises";
import type { CapabilityContract, SkillRecord } from "../domain.js";
import { exportSkill, normalizeSkillName } from "../export/skill-exporter.js";
import { ensureDir, readJson, writeJson } from "../utils.js";

async function exists(target: string) {
  try {
    await stat(target);
    return true;
  } catch (error: any) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

function assertInside(root: string, target: string) {
  const relative = path.relative(path.resolve(root), path.resolve(target));
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("Skill 目录超出允许范围");
  }
}

export class SkillLibrary {
  private readonly stateDir: string;
  private readonly registryFile: string;
  private readonly historyDir: string;
  private readonly trashDir: string;

  constructor(private readonly outputRoot: string, dataDir: string) {
    this.stateDir = path.join(dataDir, "skills");
    this.registryFile = path.join(this.stateDir, "registry.json");
    this.historyDir = path.join(this.stateDir, "history");
    this.trashDir = path.join(this.stateDir, "trash");
  }

  private async allRecords() {
    return readJson<SkillRecord[]>(this.registryFile, []);
  }

  async list(includeDeleted = false) {
    const records = await this.allRecords();
    return records
      .filter(record => includeDeleted || record.status !== "deleted")
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  }

  private async save(records: SkillRecord[]) {
    await writeJson(this.registryFile, records);
  }

  async export(name: string, capabilities: CapabilityContract[], confirmed: boolean) {
    if (!confirmed) throw new Error("导出或重新导出前必须取得明确确认");
    const skillName = normalizeSkillName(name, capabilities);
    const records = await this.allRecords();
    const previous = records.find(record => record.name === skillName && record.status !== "deleted");
    if (previous?.status === "frozen") throw new Error("该 Skill 已冻结；需要先解除冻结才能重新导出");

    const temporaryRoot = path.join(this.stateDir, "staging", `${skillName}-${Date.now()}`);
    const exported = await exportSkill(temporaryRoot, name, capabilities);
    const destination = path.join(this.outputRoot, exported.skillName);
    assertInside(this.outputRoot, destination);
    await ensureDir(this.outputRoot);

    if (await exists(destination)) {
      const historyTarget = path.join(this.historyDir, `${skillName}-v${previous?.version || 1}-${Date.now()}`);
      await ensureDir(path.dirname(historyTarget));
      await rename(destination, historyTarget);
    }
    await rename(exported.dir, destination);

    const now = new Date().toISOString();
    const record: SkillRecord = {
      id: previous?.id || `skill-${skillName}`,
      name: skillName,
      displayName: name.trim() || skillName,
      directory: destination,
      version: (previous?.version || 0) + 1,
      status: "active",
      capabilityIds: exported.capabilityIds,
      routeIds: exported.routeIds,
      exportedAt: now,
      updatedAt: now
    };
    const next = records.filter(item => item.id !== record.id);
    next.push(record);
    await this.save(next);
    return {
      ...record,
      count: exported.count,
      primaryCount: exported.primaryCount,
      lookupCount: exported.lookupCount,
      primaryCapabilityIds: exported.primaryCapabilityIds,
      lookupCapabilityIds: exported.lookupCapabilityIds
    };
  }

  async setFrozen(name: string, frozen: boolean, confirmed: boolean) {
    if (!confirmed) throw new Error("变更冻结状态前必须取得明确确认");
    const records = await this.allRecords();
    const record = records.find(item => item.name === name && item.status !== "deleted");
    if (!record) throw new Error("Skill 不存在");
    record.status = frozen ? "frozen" : "active";
    record.frozenAt = frozen ? new Date().toISOString() : undefined;
    record.updatedAt = new Date().toISOString();
    await this.save(records);
    return record;
  }

  async delete(name: string, confirmed: boolean) {
    if (!confirmed) throw new Error("删除 Skill 前必须取得明确确认");
    const records = await this.allRecords();
    const record = records.find(item => item.name === name && item.status !== "deleted");
    if (!record) throw new Error("Skill 不存在");
    assertInside(this.outputRoot, record.directory);
    let recoverableFrom: string | undefined;
    if (await exists(record.directory)) {
      recoverableFrom = path.join(this.trashDir, `${record.name}-v${record.version}-${Date.now()}`);
      await ensureDir(path.dirname(recoverableFrom));
      await rename(record.directory, recoverableFrom);
    }
    record.status = "deleted";
    record.deletedAt = new Date().toISOString();
    record.updatedAt = record.deletedAt;
    record.recoverableFrom = recoverableFrom;
    await this.save(records);
    return record;
  }

  async invocation(name: string, goal: string) {
    const record = (await this.list()).find(item => item.name === name);
    if (!record) throw new Error("Skill 不存在");
    if (!goal.trim()) throw new Error("请描述要完成的业务目标");
    return {
      record,
      prompt: `请严格按照 ${path.join(record.directory, "SKILL.md")} 中的 Skill 执行以下业务目标。只使用合同中已验证的能力和 approved: true 的绑定；遇到歧义先询问，写操作执行前单独确认。\n\n业务目标：${goal.trim()}`
    };
  }
}
