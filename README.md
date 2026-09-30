# 灯光渐变时间轴（Lighting Fade Timeline）

供灯光师排演使用的 Web 应用：以“提示项（cue）”为单位安排灯具各通道的渐变，
浏览器中以原生 JavaScript 绘制通道时间轴并实时显示读数；工程通过
FastAPI + SQLite 保存，支持 Docker Compose 一键启动。

## 快速开始（Docker Compose）

```bash
docker compose up --build
```

启动后浏览器访问：

- 应用界面：<http://localhost:8000/>
- OpenAPI 文档：<http://localhost:8000/docs>

SQLite 数据文件保存在命名卷 `lighting-data`（容器内 `/data/lighting.db`），
`docker compose down` 不会删除；如需清空：`docker compose down -v`。

## 本地直接运行（不用 Docker）

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
LIGHTING_DB=./lighting.db uvicorn app.main:app --reload --port 8000
```

访问 <http://localhost:8000/>。首次启动会自动建表并写入一个“演示工程”。

## 功能说明

### 提示项编辑

每个提示项包含：灯具、通道、目标值（0–255 整数）、渐变时长（非负整数毫秒）、
起点。起点有两种形式：

- **绝对时间**：非负整数毫秒；
- **引用另一项**：选择被引用项 + 锚点（该项的“开始”或“结束”）
  + 非负整数毫秒偏移。实际起点 = 被引用项实际开始（或开始+时长）+ 偏移。

可在右侧表格中增、删、改；编辑时会去抖地“只校验、不落库”实时预览，
点 **保存工程** 才整体原子提交，点 **校验** 可查看当前全部错误。

### 保存规则（失败绝不改变原工程）

保存（PUT）时后端先对**整份新工程**做校验，任何一条不满足都返回 400，
之后才在单个 SQLite 事务中替换数据；因此被拒绝的保存不会改动任何已有数据：

- 目标值必须是 0–255 的整数；时长/时间/偏移必须是非负整数；
- 引用必须指向一个**存在的其他项**（删除被引用项会被拒绝，
  前端删除行后引用项会出现“引用的项不存在”的行级报错）；
- 不允许引用环（含自引用）；
- 同一灯具同一通道不允许在**同一实际时刻**有两项起步
  （即使其中一项是通过引用间接得到的相同时刻）。

保存成功后，所有实际时刻、接续值、打断标记都会基于最终工程重新计算。

### 渐变求值语义

- 每个灯具通道初值为 **0**；
- 新提示项从其起步时刻上一渐变的**未取整值**接续，做线性渐变；
  因此中途被打断再续接不会累积四舍五入误差；
- **渐变时长为 0** 表示在起步时刻立即切换到目标值；
- 可在任意非负时刻（允许小数，内部按精确分数计算）求值；
- 读数显示时四舍五入到整数，**恰好 0.5 向上**（非银行家舍入）。

前端使用 BigInt 分数运算，后端使用 Python `fractions.Fraction`，
跳转/播放时浏览器会把同一时刻发回 `/evaluate` 交叉核对，
读数旁显示“✓ 与服务端一致”。小数时间按 IEEE-754 double 的精确值在两侧解析，
结果逐位相同。

### 时间轴与播放

- 顶部时间轴按灯具/通道分行，蓝线为线性渐变，金色为零时长立即切换，
  橙色斜纹虚线段是**被后一项打断、实际不会执行**的渐变尾部；
- 点选任意提示项（表格行或时间轴块），紫色虚线箭头标出完整
  **引用链**（上游祖先与下游引用者），表格中同步高亮；
- 可在时间轴上按住拖动播放头、用 **播放 / 暂停 / 归零** 控制，
  或在时间框输入毫秒后 **跳转**；
- 拖动跳转后的读数与对该时刻直接求值完全相同（同一套精确引擎）。

## HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/projects` | 工程列表 |
| POST | `/api/projects` | 新建工程（整体校验，失败 400） |
| GET | `/api/projects/{id}` | 工程 + 编译后的时间轴 |
| PUT | `/api/projects/{id}` | **原子**整体替换（先校验，失败不改库） |
| POST | `/api/projects/{id}/validate` | 只编译校验，不写库 |
| POST | `/api/projects/{id}/evaluate` | 按时刻求值，`time` 为非负数或 `{n,d}` 分数 |

求值请求示例：

```bash
curl -X POST http://localhost:8000/api/projects/demo/evaluate \
  -H 'Content-Type: application/json' -d '{"time": 1250.5}'
# 或精确分数（1250.5 = 2501/2）
curl -X POST http://localhost:8000/api/projects/demo/evaluate \
  -H 'Content-Type: application/json' -d '{"time": {"n": 2501, "d": 2}}'
```

## 测试

```bash
pip install pytest httpx
python3 -m pytest tests/                 # 34 个引擎/API 测试
python3 scripts/gen_vectors.py           # 生成 Python 精确求值向量
node tests/cross_verify.mjs              # JS(BigInt) 与 Python 逐位比对
node tests/dom_smoke.mjs                 # 前端加载/编辑/播放冒烟（DOM 桩）
```

## 目录结构

```
app/            FastAPI 应用、SQLite 层、精确求值引擎（Python Fraction）
static/         原生 JS/CSS/HTML 前端（BigInt 有理数引擎，无框架）
tests/          pytest 引擎与 API 测试、Node 交叉验证与 DOM 冒烟
scripts/        跨语言测试向量生成
Dockerfile, docker-compose.yml, requirements.txt
```
