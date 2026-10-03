# 办理顺序

一个 Skill 是这次目标的整段办理。成品只有 `SKILL.md`、`scripts/client.py`、`references/api.md`。

`SKILL.md` 第一段是默认完整办理，后面才是单条命令。每条命令一行 `python scripts/client.py`。

写操作含 `--confirm`。未确认不发送 POST、PUT、PATCH、DELETE。

只有证据里对上的绑定才把上一步的值传入下一步。没有绑定就停下来问，不要猜传值。

任一步失败就停，保留已经完成的结果。

不写「每次只执行一项」「不得自行串联」。
