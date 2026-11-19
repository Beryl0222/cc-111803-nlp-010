# 会议纪要中的承诺与后续兑现状态

本地文本工作区用于保存决定与建议语气识别相关的合成记录，并提供基础规范化和词项查看能力。

## 环境

需要 Python 3.11 或更新版本。运行程序仅使用 Python 标准库。测试依赖固定为 pytest 8.3.4，可执行 `python -m pip install -r requirements-dev.txt` 安装。

## 运行

运行 `PYTHONPATH=src python -m nlp_meeting_commitment_register.desk_cli` 查看已经确认的承诺是否能占用工位。样例按登记的出席人员核对承诺权限。前置交付以实际收件登记为准，可用工位取排期表。

带可替代路线和到货条件的完整样例用 `--case fixtures/coordination.json`：

```
PYTHONPATH=src python -m nlp_meeting_commitment_register.desk_cli --case fixtures/coordination.json
```

每条结果包含当前状态、负责人、发言性质、阻塞项与根因、被它阻塞的后续工作、依据原话和来源，便于审核人员回查是哪句话、哪位负责人、哪项到货决定了当前结论。

### 判定规则

* 提议、附条件说法、“如果……再……”都只算提议；他人代述要等本人确认；原话明确改口后以改口后的说法为准。
* 原话中出现的人名和日期只作为线索保留，负责人取登记角色，工位和班次取结构化字段，不会因为原话提到就写进日程。
* 到货等条件只有状态为已确认/已到货才算落实；写成“承诺周三到货”仍是等待前置交付。
* 同一交付有多条可替代路线时，选定前各路线都停在“等待选择”，不占工位；两条路线可共用的准备工作照常标为“准备中”。
* 只有被选中路线的前置条件满足后才排入工位；该路线声明的 `replaces` 能把原计划的一项前置换掉。
* 已由独立任务占用的班次不会被原计划重新生效挤掉，只会标为“需要重排”；已暂缓的前置会让依赖它的排期重排，但已兑现的设计等交付照常结清。

从项目根目录执行 `PYTHONPATH=src python -m nlp_meeting_commitment_register.cli ingest fixtures/records.json` 导入样例，随后执行 `PYTHONPATH=src python -m nlp_meeting_commitment_register.cli list` 查看记录。工作数据写入 `var/records.json`。业务查询使用 fixtures/business.json 中的初始资料，约定见 fixtures/business-contract.md。执行 PYTHONPATH=src python -m nlp_meeting_commitment_register.cli business 可查看当前业务目录结果。

样例为本地合成文本。记录包含来源、修订号和原文，当前工作区对空白做统一处理，并提供字符及英文词项切分。

## 测试

执行 `python -m pytest -q` 检查规范化、记录持久化和来源筛选，以及语气辨认、路线选择、条件落实和工位不被挤占的规则。
