# Z-Blog-We
折腾 Z-Blog 的我们……

## 简介

你可以向本仓库提交你的 RSS 或 Z-Blog 应用（插件/主题）；

### 提交流程

[New Issue](https://github.com/wdssmq/Z-Blog-We/issues/new "New Issue")

1. 新建 issue，标题必须以 `[RSS]` 或 `[APP]` 开头。
2. 使用对应模板填写数据。
3. 维护者通过标签审核数据：
	- `pick`: 接受并额外标记
	- `def`: 接受
	- `del`: 拒绝

### 数据规则

RSS:
- 必填: 博客名称、描述、tags、RSS 地址
- `tags` 最多 4 个

APP:
- 必填: 应用名称、描述、tags、Git 仓库地址
- `tags` 最多 4 个
- 第一个 tag 必须是 `plugin` 或 `theme`

一致性规则:
- 标题前缀与数据 `type` 必须一致
