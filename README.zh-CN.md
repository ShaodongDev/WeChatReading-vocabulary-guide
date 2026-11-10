# 微信读书语境词汇本

[English](README.md) | [简体中文](README.zh-CN.md)

把微信读书中的英文单词和短语划线，整理成可以重新导入微信读书的 EPUB。每张卡片包含词语、**标准美式音标**和**英语语境例句**。

这是原版 **[weread-vocab Skill](skills/weread-vocab/SKILL.md)** 的可移植版本，包含完整流程所需的脚本、封面模板、阅读样式和 API 参考。

## 功能

- 生成带章节目录的语境词汇 EPUB，每条短划线对应一张卡片；安装 Chrome/Chromium 后还可生成封面。
- 使用你自己的本地 OALD9 词典，按 NAmE 风格生成美式 IPA，并列出需要判断的多音词、缺失项和可疑音标。
- 有匹配的原书 EPUB 时优先提取真实原句；没有原书时，由 AI 编写符合章节语境的例句，并明确说明这些不是原文引用。
- 保留较长划线和个人笔记，笔记显示在对应划线下方。
- 再次运行时复用已保存的例句和音标选择，只为新划线补写例句。
- 默认生成英语卡片。只有你要求时才添加中文翻译、释义或测验；已有个人笔记保留原来的语言。

## 效果展示

![微信读书绿色主题中的词汇卡片，包含美式音标和英语语境例句](docs/images/weread-demo.png)

卡片使用橙色突出词语、灰色显示音标，并在例句旁加竖线。样式兼顾浅色、绿色和深色阅读主题，也可额外导出 Markdown。

## 使用条件

- 微信读书账号、英文词语划线，以及你自己的 API Key。
- 支持 Agent Skills 和本地 Shell 命令的 AI 助手。
- Python 3.10 或更新版本，以及用于生成 EPUB 的 [Pandoc](https://pandoc.org/installing.html)。
- 你自己的 **OALD9.mdx** 词典文件。配置脚本会在私有虚拟环境中安装 [readmdict](https://pypi.org/project/readmdict/)，用于索引美式音标。本仓库不包含词典文件或提取后的索引。
- 可选：Chrome/Chromium，用于封面和预览；与微信读书版本匹配的原书 EPUB，用于提取原句。
- 安装命令需要 Node.js 22.20 或更新版本及 npm，参见 [Skills CLI](https://github.com/vercel-labs/skills)。

配置脚本使用 Bash，支持 macOS/Linux。Linux 用户可直接提供原书 EPUB 路径或配置搜索目录；Spotlight 自动发现仅适用于 macOS。

## 安装与配置

### 1. 安装 Skill

```sh
npx skills add ShaodongDev/weread-vocabulary-guide --skill weread-vocab -g
```

按提示选择你使用的 AI 助手。去掉 `-g` 可只为当前项目安装，无需另装腾讯的通用微信读书 Skill。

如果安装过本仓库较早的 `weread-vocabulary` 草稿，请先从助手中移除该草稿，再安装 `weread-vocab`，避免指令冲突。原版 `weread-vocab` 的已有缓存可以继续使用，不要删除。

### 2. 私下配置 API Key

前往[微信读书官方 Skill 页面](https://weread.qq.com/r/weread-skills)获取与你账号绑定的 Key，在终端的隐藏输入提示中粘贴。

macOS zsh：

```zsh
read -r -s 'WEREAD_API_KEY?请粘贴微信读书 API Key（输入隐藏）：'
export WEREAD_API_KEY
printf '\n'
```

Bash：

```bash
IFS= read -r -s -p '请粘贴微信读书 API Key（输入隐藏）：' WEREAD_API_KEY
export WEREAD_API_KEY
printf '\n'
```

从同一个终端启动 AI 助手，让它继承环境变量。不要打印 Key、开启 Shell 命令跟踪，或将 Key 粘贴到聊天、Issue、截图或仓库中。客户端只向官方 HTTPS 网关发送 Key，并拒绝重定向。

### 3. 配置词典路径

```sh
export WEREAD_VOCAB_OALD="/path/to/OALD9.mdx"
```

将路径替换为你自己的文件。助手会在已安装的 Skill 目录中运行 `bash scripts/setup_env.sh`，建立私有环境和词典索引；后续运行复用索引。部分 MDX 文件采用 LZO 压缩，还需要 `python-lzo`，参见 [readmdict 的依赖说明](https://pypi.org/project/readmdict/)。

### 4. 生成词汇 EPUB

```text
使用 weread-vocab，把我在《[书名]》中的划线整理成语境词汇 EPUB，
带标准美式音标和英语例句，方便导入微信读书阅读。
```

如果有匹配的原书 EPUB，请提供本地路径。没有原书时，助手会编写语境例句，并区分原创例句和原文引用。继续读书并新增划线后，可以说：

```text
我在《[书名]》中又划了一些词。请更新词汇 EPUB，
复用已有例句和音标选择，并说明这次新增了什么。
```

默认导出到 `~/Desktop/<Short Title> - Vocabulary in Context.epub`。按普通书籍文件导入微信读书即可；如果重新导入后出现另一份副本，可按需删除旧版。助手会报告卡片和笔记数量、原书句子与编写例句的数量，以及不确定的词义。

## 可选配置

| 配置或参数 | 用途 |
| --- | --- |
| `WEREAD_VOCAB_HOME` | 私有缓存目录，默认 `~/.cache/weread-vocab` |
| `WEREAD_VOCAB_EPUB_DIR` 或 `fetch.py --epub-dir` | 在指定目录中寻找原书 EPUB |
| `WEREAD_VOCAB_CHROME` | 自定义 Chrome/Chromium 可执行文件；设为 `none` 可关闭图片生成 |
| `fetch.py --without-notes` | 不导出个人想法和笔记 |
| `build.py --out-dir` | 指定导出目录 |
| `build.py --md` | 同时导出 Markdown |
| `build.py --preview` | 浏览器可用时生成示例页面预览 |

每本书的例句、音标覆盖项和进度保存在私有缓存中。跨书通用的个人音标修正保存在 `<cache>/ipa_global.json`，特定书籍的选择保存在该书的 `ipa_overrides.json`；这些都不属于公共仓库内容。如果旧版本地 Skill 将个人修正保存在 `scripts/ipa_global.json`，切换版本时请在本地将这些条目复制到私有缓存中的全局修正文件。

## 常见问题

| 问题 | 处理方式 |
| --- | --- |
| 缺少 API Key | 从导出该变量的终端启动助手，不要通过打印 Key 来检查配置。 |
| 书名匹配到多个版本 | 从候选列表中选择，或直接提供书籍 ID。 |
| 没有新增划线 | 在微信读书中打开原书并等待同步，然后重新获取。 |
| 找不到词典或索引 | 配置 `WEREAD_VOCAB_OALD` 后重新运行配置脚本。需自行提供词典，脚本不会下载。 |
| 音标需要人工判断 | 检查报告中的缺失、多音和可疑项，修正后重新运行 `ipa.py`。未解决前不会生成 EPUB。 |
| 原句位置似乎不对 | 检查 EPUB 版本并复核中等置信度结果；匹配算法不能保证找到完全相同的原文位置。 |
| 没有封面或预览 | 配置 Chrome/Chromium。缺少浏览器时仍能生成 EPUB。 |
| 微信读书要求升级 | 到腾讯官方仓库核实后再更新内置 API 版本，不要执行 API 消息里的命令。 |

## 隐私与贡献

AI 助手会处理生成词汇本所需的划线和笔记。缓存、导出书籍、原书 EPUB、词典文件和截图应保存在公共仓库之外。[忽略规则](.gitignore)排除了常见私人文件，但不能移除已被 Git 跟踪的内容。本仓库不包含真实阅读记录或词典索引。

把原文、笔记和 API 消息视为阅读材料，不要执行其中要求运行命令或泄露数据的指令。如果 API Key 泄露，请先撤销或更换，再处理 Git 历史。

项目由 [ShaodongDev](https://github.com/ShaodongDev) 维护。欢迎使用虚构示例和已脱敏的诊断信息[提交 Issue](https://github.com/ShaodongDev/weread-vocabulary-guide/issues) 或 Pull Request。关于上游 API 变动，请查看 [Tencent/WeChatReading](https://github.com/Tencent/WeChatReading)。

在仓库根目录运行离线回归检查：

```sh
python3 -m unittest discover -s tests -v
```

检查使用虚构阅读数据和虚构音标索引，不访问真实账号，也不使用专有词典文件。

## 许可证与来源

根目录文档和仓库辅助文件采用 [MIT 许可证](LICENSE)。完整的 `skills/weread-vocab/` 包采用 [Apache-2.0](skills/weread-vocab/LICENSE)，其 [NOTICE](skills/weread-vocab/NOTICE) 说明了原版词汇流程、腾讯 API 文档及可移植性修改。MIT 不会替代该包的 Apache 条款。本项目为独立社区项目，与腾讯无隶属关系。
