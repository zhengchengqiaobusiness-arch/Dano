<script lang="ts">
  import skillIcon from "../assets/skill-icon.svg";

  let {
    skillName,
  }: {
    skillName: string;
  } = $props();
</script>

<div class="skill-invocation-card">
  <img src={skillIcon} alt="skill" class="skill-icon" />
  <span class="skill-name">{skillName}</span>
</div>

<style>
  .skill-invocation-card {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    border-radius: 6px;
    background: var(--panel-2, #f6f8fa);
    border: 1px solid var(--border, #d0d7de);
    font-size: 13px;
    color: var(--text, #1f2328);
  }

  .skill-icon {
    width: 16px;
    height: 16px;
    flex-shrink: 0;
  }

  .skill-name {
    font-weight: 500;
  }
</style>
