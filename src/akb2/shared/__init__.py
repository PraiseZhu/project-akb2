"""跨链路共享件（briefing / quality 都要消费、互不依赖）。

`akb2.common` 放与业务无关的基础设施（路径 / 预算闸）。本包放 twitter-cli
等被多条业务链路共用的具名常量与适配器，避免 quality 为读签名去 import briefing。
"""
