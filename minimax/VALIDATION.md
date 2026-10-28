# 验证样例：库存下限筛选

## 任务与保持行为

给 `catalog.service.list_products` 增加 `min_stock`：非负整数、包含边界的 `stock >= min_stock`，`None` 保留原行为。负数、布尔值、小数、字符串和容器必须抛出带 `min_stock` 的 `ValueError`，空商品列表也须校验；整数无上限。

原行为必须保持：大小写无关的 Unicode casefold 子串查询、默认隐藏停用商品、显式包含停用商品、稳定输入顺序、不修改输入、返回新列表。所有条件按 AND 组合。

初始数据在 `sample/products.json`：

| SKU | 名称 | 库存 | 启用 |
| --- | --- | ---: | --- |
| A | Ocean Notebook | 0 | 是 |
| B | Blue Pen | 5 | 是 |
| C | Ocean Pencil | 10 | 是 |
| D | Old Ocean Pen | 8 | 否 |
| E | Straße Paper | 5 | 是 |

## 上下文必须包含的真实来源

| 来源 | 用途 |
| --- | --- |
| `specs/catalog.json#INV-001` | 包含边界、None、0、无上限 |
| `specs/catalog.json#INV-002` | 参数校验、拒绝 bool、空输入仍须校验 |
| `specs/catalog.json#INV-003` | 保留原有行为和筛选组合 |
| `catalog/service.py#list_products` | 需要修改的函数 |
| `tests/test_min_stock.py#test_inclusive_threshold` | 目标功能测试 |
| `tests/test_regression.py#test_default_behavior` | 原行为回归测试 |

这些标识写在 `sample/task.json.required_sources`，检索缺失时模块报错；`context.sources` 还包含相关代码、全部测试及数据。来源路径均相对于样例项目根目录，行号来自实际文件。退役条目 `INV-OLD` 声称使用严格大于，必须排除。

## 操作和预期

运行 README 中的 demo 命令。脚本在新目录中按以下顺序执行，每个 CLI 调用都是独立进程：

1. 复制业务项目、初始数据和固定测试，恢复交付的修改前代码；从空 seekdb、空 PowerContext 开始。
2. 导入资料，查询上下文，核对全部必要来源；初始任务状态应为空。
3. 运行 21 项目标测试和 8 项回归测试。修改前目标测试 20 失败、1 通过，回归全部通过。失败必须是未实现库存筛选或校验引起的功能失败。
4. 保存完成的规格核对及修改前验证结果。新进程重新查询，必须恢复准确记录引用、已完成项、待办及修改前结果。
5. `live` 模式把当前检索上下文传给 MiniMax，限定修改 `catalog/service.py`；`recorded` 模式核对并应用已保存的真实 MiniMax 代码响应；`reference` 模式只验证 Codex 参考补丁，明确不计 MiniMax 使用。
6. 保存前后代码、diff；重新导入修改后的代码；运行完全相同的目标与回归测试，要求 21 + 8 项全部通过。脚本对比前后测试文件 SHA-256，不允许修改测试。
7. 保存验证结果。新进程恢复后，已有结果应适用于当前快照，已完成事项可复用。
8. 将 `INV-003` 更新为 v2，增加“相同库存商品也保留原始相对顺序”。未导入前请求上下文应失败；重新导入后必须出现新文字和 v2，而旧验证应标为过期。
9. 再次验证全部测试，新进程应读取并使用 v2 的验证结果。

典型结果：`min_stock=5` 返回 B、C、E；`min_stock=0` 保留 A；`min_stock=11` 返回空；`query=oCeAn,min_stock=5` 返回 C；`include_inactive=True,min_stock=8` 返回 C、D。

## 结果核对和重置

日志 JSON 记录可复现命令、退出码、对应代码文件和测试文件的 SHA-256，以及导入快照。`code_version_sha256` 是这些代码哈希组成的内容版本；Git 提交字段是运行时仓库的基线，不代替文件哈希。

`technology-events.jsonl` 中的 `seekdb.query.result_sources` 应与上下文返回来源吻合，查询的 `scope_id` 和 `active_flag=1` 必须生效。`powercontext.write` 和后续不同 PID 的 `powercontext.read` 应引用相同的持久记录，并被后续上下文实际使用。

重置时使用新的 `--run-dir`，即可从交付的修改前快照重做；无需任何未交付的本机文件或历史数据库。真实 MiniMax 开发需要用户自己的密钥；审核现有真实开发记录与执行其补丁不需要密钥。

