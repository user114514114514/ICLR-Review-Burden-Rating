[EN](../README.md) | **[ZH](./README.zh.md)**

# ICLR Review-Burden Rating

![ICLR Review-Burden Score](./image.png)

基于公开的 [qhjqhj00/iclr-openreview-reviews](https://github.com/qhjqhj00/iclr-openreview-reviews) 数据集，量化低质量投稿行为给评审社区带来的可能负担的本地离线工具。

> **关于设计动机与说明**：本 README 仅包含快速部署指引与算法数学概要。关于项目的深层动机、伦理边界、实测分数分布、以及为什么单纯数投稿量或计算拒稿率会失效等详细讨论，请在网页中查看 **[Blog](http://127.0.0.1:8765/essay.html)** ，或直接参阅 `[docs/blog_zh.md](./blog_zh.md)` / `[docs/blog_en.md](./blog_en.md)`。

---

## 快速上手与部署指引

**环境要求**：Python 3.10+。不需要任何额外库，只用标准库。

### 1. 启动 Web 界面

```bash
python3 -m iclr_burden serve
```

在浏览器打开 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)。本地服务为只读模式，提供作者检索、年度得分卡、峰值分排名与全库分数分布等功能。

### 2. 命令行查询

```bash
# 按学者姓名或 OpenReview Profile ID 搜索
python3 -m iclr_burden search 'Family'

# 查看单人详细报告
python3 -m iclr_burden author --author '~Given_Family1'

# 按峰值分（Peak Score）查看前 K 位排名
python3 -m iclr_burden top-s --k 20

# 按指定年份年度分（Annual Score）查看排名
python3 -m iclr_burden top-a --year 2026 --k 20

# 查看单篇论文详情
python3 -m iclr_burden paper --paper PAPER_ID
```



### 3. 数据库维护（可选）

预计算的 SQLite 数据库位于 `data/iclr.sqlite3`。库里只保存网站查询用到的字段：作者身份、论文分数，以及每条审稿的分数和置信度，不含论文正文和审稿意见。如需下载固定快照并重建：

```bash
python3 -m iclr_burden dataset-setup
```

---



## 算法概要 (`score-v1.8`)

评分数据严格限定在 **[ICLR](https://iclr.cc/) 2024–2026**，且仅计入明确绑定到规范 [OpenReview](https://openreview.net/) Profile ID（`~Given_Family1`）的作者署名位置。

### 1. 单篇鲁棒评分 ($`R_p`$)

审稿评分（$`r_i \in [1, 10]`$）与置信度（$`c_i \in [1, 5]`$）在加权前被截断在该论文中位数 $`m_p`$ 的上下 2 分之内：

```math
q_i = 0.6 + 0.1 c_i, \quad R_p = \frac{\sum_i q_i \cdot \operatorname{clip}(r_i, m_p - 2, m_p + 2)}{\sum_i q_i}
```



### 2. 年度动态基准校准

每年仅以当年录用论文（Accepted Papers）的四分位数 $`Q_{25}`$ 和 $`Q_{75}`$ 进行自适应校准：

- 尺度参数：$`s_y = \max\left(\frac{Q_{75} - Q_{25}}{1.349}, 0.5\right)`$
- 合理投稿底线：$`T_y = Q_{25} - s_y`$
- 优质论文基准：$`H_y = Q_{75}`$



### 3. 原始亏空 ($`p_p`$) 与饱和单篇严重度 ($`h(p)`$)

针对跌破 $`T_y`$ 的拒稿或撤稿（录用论文 $`p_p = 0`$）：

```math
p_p = \max\left(0, \frac{T_y - R_p}{s_y}\right)
```

单篇亏空经由有界的单篇饱和严重度函数映射至 $[0, 0.5)$：

```math
h(p) = \frac{p}{1 + 2p}
```



### 4. 复利累乘低质负担 ($`B`$)

作者署名位次系数 $`\alpha_k`$ 严格位于 $`h(p)`$ 变换之外：第一作者 $`\alpha_1 = 1.0`$，第 2–4 作者 $`\alpha_{2..4} = 0.3`$，第 5 作者及以后 $`\alpha_{\ge 5} = 0.1`$。

```math
B = 4 \left[ \prod_{p \in L} \left(1 + \alpha_k \cdot \frac{p_p}{1 + 2p_p}\right) - 1 \right]
```

单篇因子天然受限（第一作者上限 1.5，第 2–4 作者 1.15，第 5 作者及以后 1.05）。同一周期内的多次低质投稿主导少数几篇极端低分。

### 5. 年度总分 ($`A`$)

```math
A = (1 + \rho) \max(0, B - G) + N_{\text{bad}} \cdot D
```

- 结构比率 $`\rho = \frac{N_{\text{bad}}}{N_{\text{bad}} + N_{\text{acc}}}`$，其中 $`N_{\text{bad}} = |L| + N_{\text{DR}}`$
- 优质冲抵信用 $`G = \min(3.0, \sum \beta_k g_p)`$，由高于 $`H_y`$ 的录用论文经 $`\tanh`$ 封顶产生
- 格式拒稿项 $`D = 0.1 \sum \alpha_k`$



### 6. 跨年峰值分（Peak Score）排名

每年完全独立核算，绝不跨年累加。跨年排名统一采用**峰值分（Peak Score）**：

```math
\text{Peak Score} = \max_{y \in \text{有投稿年份}} A_y
```

---



## 数据集与快照

- **数据源**：[qhjqhj00/iclr-openreview-reviews](https://github.com/qhjqhj00/iclr-openreview-reviews)（`v1.0`，快照日期 2026-05-08）。稿件、评审、决议和作者字段都来自这份 [OpenReview](https://openreview.net/) 快照。运行时不会再去查询 OpenReview 个人主页。
- **作者身份**：每篇投稿上的 `authorids`。只有规范的 [OpenReview](https://openreview.net/) Profile ID（`~Given_Family1`）才计入个人分数。邮箱 ID、缺失 ID，以及和 `authors` 名单对不齐的位置，都不计入个人分。页面上的名字是从 Profile ID 拆出来的。搜索还会匹配同一篇投稿里的 `authors` 署名字符串，匹配只用于查找，不会把两个 ID 合并成同一个人。
- **核算规模**：[ICLR](https://iclr.cc/) 2024–2026（38,890 篇投稿，93,515 位评分作者，167,410 条作者-年份记录）

