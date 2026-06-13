# SP1 — Foundations（config + seed + abbr）设计

> 依赖：无。被依赖：全部。这是最小、最稳定的地基层。先建它。

## 1. 目标与边界

提供三件事，别的不做：

1. **配置**：从环境/`.env`/默认值加载运行参数（`dext.config`）。
2. **Seed 加载**：解析校验 `entrances.yaml`，给出强类型对象（`dext.seed`）。
3. **简称解析**：把学校名/官网 URL 映射为英文简称 `abbr`，用于 DB 文件名（`dext.seed` 内）。

不含：DB、网络、LLM、任何 I/O 副作用（除读 YAML/env）。

## 2. 配置 `dext.config`

用 `pydantic-settings`。单一 `Settings`，从环境变量 + `.env` + 默认值读取，前缀 `DEXT_`（LLM key 例外，用约定俗成的 `DEEPSEEK_API_KEY`）。

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEXT_", env_file=".env",
                                      env_file_encoding="utf-8", extra="ignore")

    # 路径
    data_dir: Path = Path("data/universities")
    seed_path: Path = Path("entrances.yaml")        # 不存在时回退 assets/entrances.yaml

    # bridge 服务器（契约固定，但端口/host 可配）
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 21520
    fetch_timeout_seconds: int = 60                 # job 超时

    # LLM（DeepSeek / OpenAI chat completions）
    deepseek_api_key: str = Field(default="", validation_alias="DEEPSEEK_API_KEY")
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"                # 默认不思考
    llm_model_retry: str = "deepseek-reasoner"      # 重试启用 Low 思考
    llm_enable_thinking: bool = False
    llm_workers: int = 4                            # 并发抽取 worker 数
    invalid_json_max_retry: int = 2

    # 调度 / 重试
    max_depth: int = 4
    max_attempts: int = 3
    followup_page_limit: int = 36                   # 源文档 §11.2
    attempt_penalty: float = 5.0                    # priority = base - attempt*penalty

    # 日志
    log_level: str = "INFO"
```

- 唯一访问入口 `get_settings()` 返回缓存单例（`functools.lru_cache`）。
- 缺 `DEEPSEEK_API_KEY` 时**不**在导入期报错；在真正调用 LLM 前由 SP5/SP7 校验并给出清晰提示（KISS：不让纯数据层依赖密钥）。

## 3. Seed 加载 `dext.seed`

### 3.1 模型（pydantic v2）

镜像 `entrances.yaml` 两种入口形态：

```python
class OrgUnitSeed(BaseModel):
    name: str
    kind: Literal["college","department","institute","hospital"] | str = "college"
    url: str | None = None              # 已知学院主页（可选）
    faculty_urls: list[str] = []        # 已知师资列表页（可选）

class UniversitySeed(BaseModel):
    name: str
    url: str                            # 学校官网，abbr 来源
    location: str | None = None
    abbr: str | None = None             # 可选显式覆盖；否则从 url 派生
    org_unit_listing_urls: list[str] = []   # 机构列表页入口
    org_units: list[OrgUnitSeed] = []       # 已知学院入口

    @model_validator(mode="after")
    def _at_least_one_entry(self): ...   # 三类入口至少有一个非空

class Manifest(BaseModel):
    version: int = 1
    universities: list[UniversitySeed]
```

### 3.2 加载函数

```python
def load_manifest(path: Path | None = None) -> Manifest
def get_university(manifest: Manifest, name: str) -> UniversitySeed   # 精确名匹配，找不到抛清晰错误
```

- UTF-8 读取，`yaml.safe_load`。
- `path` 为空时按 `settings.seed_path` → 回退 `assets/entrances.yaml`。
- 校验失败抛带行为提示的 `SeedError`（哪个学校、缺什么）。

### 3.3 与真实 seed 的对齐（已验证要点）

- 多数学校用 `org_unit_listing_urls`；XJTU/XMU/SYSU 用 `org_units[].faculty_urls`。模型两者都支持，互不排斥。
- 学院名可能带校区前缀（"珠海-数学学院"）——按原样存，不拆分（拆分是 SP2/SP6 的去重关注点，不在 seed 层）。
- `faculty_urls` 可能带重查询串、非 `.edu.cn` 域（`dentalxjtu.com`）——seed 层只做字符串校验（非空、看起来是 URL），**不**规范化（规范化属 SP3）。

## 4. 简称解析（abbr）

`abbr` 决定 DB 文件名 `data/universities/<abbr>.db`，必须稳定、可读、唯一。

**规则（KISS）：**
1. 若 seed 显式给了 `abbr`，直接用（小写、去空白）。
2. 否则从 `university.url` 的主机名派生：取**公共后缀（`.edu.cn` / `.edu` / `.com` …）左侧紧邻的标签**。
   - `https://www.buaa.edu.cn/` → `buaa`
   - `https://www.pku.edu.cn/` → `pku`
   - `https://www.dlut.edu.cn/` → `dlut`（即使 seed 里另有 `panjin.dlut.edu.cn` 子域，官网 url 是 www.dlut）
3. 结果做 slug 化：仅 `[a-z0-9-]`，其余转 `-`，压缩连续 `-`。

```python
def resolve_abbr(university: UniversitySeed) -> str
```

- 用 `urllib.parse` 取 host；去掉前导 `www.`；用一张小的公共后缀集合（`edu.cn`,`edu`,`ac.cn`,`com`,`org`,`net`,`cn`）匹配最长后缀，取其左邻标签。
- **不引入** `tldextract` 等依赖（39 所学校够用，过早优化）。若未来出现冲突，用 seed `abbr` 字段手动解决。
- 提供 `def db_filename(abbr: str) -> str` 返回 `f"{abbr}.db"`（路径拼装在 SP2）。

## 5. 公开接口汇总

```python
# dext.config
get_settings() -> Settings

# dext.seed
load_manifest(path: Path | None = None) -> Manifest
get_university(manifest: Manifest, name: str) -> UniversitySeed
resolve_abbr(university: UniversitySeed) -> str
db_filename(abbr: str) -> str
```

## 6. 测试

- `resolve_abbr` 对全部 39 所 seed 学校跑一遍，断言无重复、皆非空、符合预期（buaa/pku/…）。
- `load_manifest` 解析真实 `entrances.yaml` 成功；构造缺三类入口的学校断言校验失败；构造带 `org_units` 与带 `org_unit_listing_urls` 两形态都通过。
- `get_university` 命中/未命中（清晰错误）。
- Settings：env 覆盖默认值、缺 key 不抛错。

## 7. 不做

- ❌ 多 manifest 合并、远程 seed、seed 热重载。
- ❌ URL 规范化（属 SP3）。
- ❌ 依赖 tldextract / 复杂域名库。
