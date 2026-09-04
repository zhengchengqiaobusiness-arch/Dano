import path from "node:path";
import { readdir, rename, stat } from "node:fs/promises";
import type { CapabilityContract, EvidenceEvent, SkillListItem, SkillRecord } from "../domain.js";
import { exportSkill, normalizeSkillName } from "../export/skill-exporter.js";
import { ensureDir, readJson, writeJson } from "../utils.js";
import { moveDirectory } from "./skill-files.js";
import { materializeSkillCredentials, requiredCredentialOrigins } from "../credentials/credential-store.js";

async function exists(target: string) {
  try {
    await stat(target);
    return true;
  } catch (error: any) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

async function countFiles(directory: string) {
  try {
    const entries = await readdir(directory, { withFileTypes: true, recursive: true });
    return entries.filter(entry => entry.isFile()).length;
  } catch {
    return 0;
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

  constructor(private readonly outputRoot: string, private readonly dataDir: string) {
    this.stateDir = path.join(dataDir, "skills");
    this.registryFile = path.join(this.stateDir, "registry.json");
    this.historyDir = path.join(this.stateDir, "history");
    this.trashDir = path.join(this.stateDir, "trash");
  }

  private async allRecords() {
    return readJson<SkillRecord[]>(this.registryFile, []);
  }

  async list(includeDeleted = false): Promise<SkillListItem[]> {
    const records = await this.allRecords();
    const visible = records
      .filter(record => includeDeleted || record.status !== "deleted")
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    return Promise.all(visible.map(record => this.enrich(record)));
  }

  private async enrich(record: SkillRecord): Promise<SkillListItem> {
    const skillFile = path.join(record.directory, "SKILL.md");
    const present = await exists(skillFile);
    let primaryIds = record.primaryCapabilityIds;
    let lookupIds = record.lookupCapabilityIds;
    if ((!primaryIds || !lookupIds) && present) {
      const contract = await readJson<{ capabilities?: Array<{ id: string; role?: string }> }>(
        path.join(record.directory, "references", "CONTRACT.json"),
        { capabilities: [] }
      );
      const capabilities = contract.capabilities || [];
      primaryIds ||= capabilities.filter(item => item.role !== "lookup").map(item => item.id);
      lookupIds ||= capabilities.filter(item => item.role === "lookup").map(item => item.id);
    }
    return {
      ...record,
      primaryCapabilityIds: primaryIds,
      lookupCapabilityIds: lookupIds,
      artifactStatus: present ? "ready" : "missing",
      fileCount: present ? await countFiles(record.directory) : 0,
      primaryCount: primaryIds?.length ?? record.capabilityIds.length,
      lookupCount: lookupIds?.length ?? 0
    };
  }

  private async save(records: SkillRecord[]) {
    await writeJson(this.registryFile, records);
  }

  async export(name: string, capabilities: CapabilityContract[], confirmed: boolean, events: EvidenceEvent[] = []) {
    if (!confirmed) throw new Error("导出或重新导出前必须取得明确确认");
    const slug = normalizeSkillName(name, capabilities);
    const records = await this.allRecords();
    const family = records.filter(record =>
      record.status !== "deleted" && normalizeSkillName(record.displayName) === slug
    );

    const temporaryRoot = path.join(this.stateDir, "staging", `${slug}-${Date.now()}`);
    const exported = await exportSkill(temporaryRoot, name, capabilities, [], events);
    const destination = path.join(this.outputRoot, exported.skillName);
    assertInside(this.outputRoot, destination);
    if (await exists(destination)) throw new Error(`导出目录已存在：${exported.skillName}`);
    const exportedIds = new Set(exported.capabilityIds);
    const exportedCapabilities = capabilities.filter(capability => exportedIds.has(capability.id));
    const credentialFile = await materializeSkillCredentials(
      this.dataDir,
      this.outputRoot,
      exported.skillName,
      exportedCapabilities.map(capability => capability.transport.origin),
      requiredCredentialOrigins(exportedCapabilities, events)
    );
    await ensureDir(this.outputRoot);
    await rename(exported.dir, destination);

    const now = new Date().toISOString();
    const record: SkillRecord = {
      id: exported.skillName,
      name: exported.skillName,
      displayName: name.trim() || slug,
      directory: destination,
      version: Math.max(0, ...family.map(item => item.version)) + 1,
      status: "active",
      capabilityIds: exported.capabilityIds,
      primaryCapabilityIds: exported.primaryCapabilityIds,
      lookupCapabilityIds: exported.lookupCapabilityIds,
      routeIds: exported.routeIds,
      exportedAt: now,
      updatedAt: now
    };
    await this.save([...records, record]);
    return {
      ...await this.enrich(record),
      count: exported.count,
      credentialFile,
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
    return this.enrich(record);
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
      try {
        await moveDirectory(record.directory, recoverableFrom);
      } catch {
        recoverableFrom = await exists(recoverableFrom) ? recoverableFrom : record.directory;
      }
    }
    record.status = "deleted";
    record.deletedAt = new Date().toISOString();
    record.updatedAt = record.deletedAt;
    record.recoverableFrom = recoverableFrom;
    await this.save(records);
    return { ...await this.enrich(record), recoverableFrom };
  }

  async invocation(name: string, goal: string) {
    const record = (await this.list()).find(item => item.name === name);
    if (!record) throw new Error("Skill 不存在");
    if (record.status === "frozen") throw new Error("Skill 已冻结，不能调用；请先重新录制并导出完整版本，或明确解除冻结");
    if (!goal.trim()) throw new Error("请描述要完成的业务目标");
    return {
      record,
      prompt: `请严格按照 ${path.join(record.directory, "SKILL.md")} 路由手册执行以下业务目标。先规划再按手册约定执行；只使用已验证主能力和 approved: true 的绑定。字段候选不是独立业务。遇到歧义先询问，写操作执行前单独确认。按需读取 references，不要一开始读完全部文件。\n\n业务目标：${goal.trim()}`
    };
  }
}
