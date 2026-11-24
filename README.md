# 流域跨部门水文测站年鉴编目台

把模拟水位纸迹、站址变更、零点联测和流量测验编为可复算的离线年鉴。使用 Python 3.11+ 与 SQLite，运行时无需网络或第三方依赖。样例中的站点、测验、日期序号和数据均为虚构。

## 快速开始

在仓库根目录执行，给演示指定一个空库路径。

```sh
python -m hydrobook --db yearbook.sqlite demo
python -m hydrobook --db yearbook.sqlite audit
python -m unittest discover -s tests -v
```

演示连续签发三版。第一版的北站峰值区间可分离，第二版在收到冲突联测后失去第 3 天共同基准下的水位，但北站由本地零点测算的累计水量仍为 37,152,000 升。第三版在争议联测撤销后恢复完整。审计从原始收据重建投影，逐版验证签发内容与前序关联。

## 档案操作

| 操作 | 命令 |
| --- | --- |
| 初始化工作库 | `python -m hydrobook --db work.sqlite init` |
| 采集一份收据 | `python -m hydrobook --db work.sqlite capture fixtures/season.json` |
| 确认站址和纸迹 | `python -m hydrobook --db work.sqlite chart-day NORTH UPPER 3` |
| 查询换算及容差 | `python -m hydrobook --db work.sqlite alignment NEW REF 6 3` |
| 按本地零点评定流量 | `python -m hydrobook --db work.sqlite rating N2 NEW 320 350 3` |
| 编目并检查缺天 | `python -m hydrobook --db work.sqlite yearbook BASIN-26` |
| 签发并取回存档文本 | `python -m hydrobook --db work.sqlite issue BASIN-26`，`python -m hydrobook --db work.sqlite issued 1` |

所有证据查询和签发可以通过 `--cut <收据序号>` 读取当时已到达的证据。日期使用业务整数日序，站段和调查的有效期用左闭右开区间。`fixtures/season.json` 展示原始收据、证据类型、业务键和修订的输入格式。收据与结构化投影在一次事务内写入。

## 业务规则

- 档案以当日站址和河段匹配纸卷。见证读数优先，同级读数冲突需复核，获选读数超出该纸卷量程时记录缺失。撤销修订保持撤销状态，迟到的低版本不改变当前结果。
- 基准联测使用已认证的测量证书及其有效期，同时约束同一连通范围内的差值。存在矛盾闭环、缺乏连通证明或超过容差时，证据会给出相应原因。
- 各流量曲线的测量点以本地零点记录。有效测验唯一时才做分段内插，超范围和相互冲突的测验保留为缺项。整数边界做向外舍入。
- 年鉴分别衡量水位和流量覆盖。演示将日代表流量乘以 86,400 秒得到日水量。只有一个测站的峰值下界严格超过其他站的峰值上界时才判定顺序。这个判断用独立的保守区间。
- 签发序列保存收据截止号、报告和摘要。同一截止号重复签发复用原版，推进截止号形成新的版本。

## 分卷收件

receive-volume 采录站点归档组的分卷封套和原始收据。封套字段为 desk、shipment、part、total、records，分卷号从 1 开始。当前收到任一分卷即按常规证据接口采录其中的记录，shipment-status 查看实际收到或缺失的分卷。可在 init 后执行 receive-volume fixtures/shipment-02.json 和 shipment-status field shift-5。
