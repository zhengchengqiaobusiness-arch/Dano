# Dano Deploy Instructions

## Validation

- 部署与验收分开：部署完成条件是健康检查与机器冒烟通过；保存收据并释放锁。随后询问是否验收本次改动，默认不验收。
- 仅在用户明确要求验收时，按本次改动选择相应浏览器、工具、图片、身份或记忆流程。验收等待、取消或工具中断不触发部署定时回滚。
- Follow the deployment target and acceptance mode in the root `AGENTS.md`. Direct deployment acceptance uses the existing service URL and OAuth configuration and leaves that service running.
- For explicitly requested isolated acceptance, remove only this run's temporary stack, images and unreferenced build layers; retain reusable base images and existing service data.
