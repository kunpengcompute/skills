# OmniSkill

OmniSkill 是一套面向 OmniOperator 向量化执行引擎的 AI 辅助技能集合，涵盖表达式/算子生态分析与表达式开发流程。

## 技能列表

### omnioperator-expression-analysis

表达式生态分析技能 —— 系统扫描 OmniOperator / Velox / Spark SQL 三方表达式支持现状，生成覆盖度 Gap 分析报告。

- OmniOperator 向量化框架已注册哪些表达式？各支持哪些类型？
- Velox 覆盖了哪些 Spark SQL 表达式？
- Omni 与 Velox / Spark SQL 标准之间的差距是什么？优先级如何？

详见 [omnioperator-expression-analysis/README.md](omnioperator-expression-analysis/README.md)

### omnioperator-status-analysis

算子生态分析技能 —— 全链路扫描 Spark / Gluten / OmniOperator / Velox 物理算子支持现状，生成覆盖度 Gap 分析报告。

- 当前 Omni 支持哪些 Spark 物理算子？
- 哪些算子只在 Gluten 侧声明了支持但执行侧还不完整？
- fallback 主要来自哪些算子或类型限制？
- 与 Velox 相比，Omni 缺哪些算子能力？

详见 [omnioperator-status-analysis/README.md](omnioperator-status-analysis/README.md)

### omnioperator-expression-dev

表达式开发技能 —— 指导在 OmniOperator 向量化执行引擎中开发新表达式/函数的完整流程。

- 需求分析 → 研究 Velox 参考实现 → 研究 OmniOperator 现有模式
- 编写设计文档 → 用户审批 → 实现函数 → 注册函数 → 编写单元测试
- 提供头文件、实现文件、测试文件模板

详见 [omnioperator-expression-dev/README.md](omnioperator-expression-dev/README.md)

### generate-sql-testcases

Spark 表达式测试 SQL 生成技能 —— 读取包含 Spark 函数信息的 CSV 文件，为每个函数批量生成 19 种数据类型的测试 SQL，并输出到新 CSV。

- 维护 ~170 个已知函数的 SQL 模板，按白名单匹配合理的数据类型组合
- 支持单/多参数函数、聚合函数、窗口函数、运算符
- 默认白名单模式（如 `abs` 只生成数值类型，`upper` 只生成字符串类型），`--all` 可退回到全类型模式
- 已有"已完成"状态的函数会被跳过，支持断点续跑

详见 [generate-sql-testcases/SKILL.md](generate-sql-testcases/SKILL.md)

### spark-omnioperator-test-case-generator

Spark OMNI 优化算子测试用例生成技能 —— 基于测试设计文档，为指定算子批量生成 4 字段标准 JSON 测试用例文件。

- 强制前置依赖：必须先存在 `spark-omnioperator-test-design-generator` 产出的测试设计文档
- 用例按「基础验证 → 功能深化 → 质量保障」三层结构排列
- 生成前展示用例标题预览等待确认；用例数 > 30 时分批交付
- 完成后执行六项强制自检（格式完整性、异常用例格式、名称-实现一致性、数据类型、功能模式、预期结果）

详见 [spark-omnioperator-test-case-generator/README.md](spark-omnioperator-test-case-generator/README.md)

## 目录结构

```
OmniSkill/
├── README.md
├── omnioperator-expression-analysis/   ← 表达式生态分析技能
│   ├── SKILL.md
│   └── README.md
├── omnioperator-expression-dev/        ← 表达式开发技能
│   ├── SKILL.md
│   ├── README.md
│   └── references/                     ← 模板与参考文档
├── omnioperator-status-analysis/       ← 算子生态分析技能
│   ├── SKILL.md
│   ├── README.md
│   └── references/                     ← 模板与参考文档
├── generate-sql-testcases/            ← Spark 表达式测试 SQL 生成技能
│   ├── SKILL.md
│   ├── scripts/
│   │   └── generate_sql_testcases.py
│   ├── examples/
│   │   ├── example_input.csv
│   │   └── example_output.csv
│   └── references/
│       └── data_types.md
├── spark-omnioperator-test-case-generator/  ← Spark OMNI 算子测试用例生成技能
│   ├── SKILL.md
│   ├── README.md
│   └── references/                     ← 规范与校验清单
│       ├── case-spec.md
│       ├── data-type-reference.md
│       ├── sql-patterns-reference.md
│       ├── case-checklist.md
│       └── data-type-checklist.md
└── .gitignore
```