import Link from "next/link";
import { Fragment } from "react";
import { siteAssetPath } from "../build/site-base-path";

const valueSteps = [
  ["说明需求", "自然语言说清办理目标"],
  ["理解意图", "识别流程、入口与规则"],
  ["引导办理", "补齐信息并集中确认"],
  ["返回结果", "状态与原系统入口回到对话"],
];

const workflow = [
  ["结构化需求", "把员工的一段话整理成完整业务字段"],
  ["员工确认", "提交前核对关键内容与业务规则"],
  ["原系统执行", "沿用身份、权限和流程完成办理"],
  ["结果回传", "单号、状态与原系统入口持续可查"],
];

const scenes = [
  ["请假", "类型、时段与事由"],
  ["公章", "用途、材料与用印时间"],
  ["会议室", "时间、地点与人数"],
  ["车辆", "路线、时间与用车事由"],
  ["报销", "票据、费用类型与金额"],
  ["流程查询", "进度、待办与结果"],
];

const departments = [
  ["人力", "入转调离、证明开具、培训报名"],
  ["财务", "预算申请、费用报销、合同付款"],
  ["采购", "采购申请、供应商准入、审批查询"],
];

const integrations = [
  ["ERP", "订单、库存与采购"],
  ["CRM", "客户、商机与跟进"],
  ["工单", "设备、账号与故障"],
  ["项目", "任务、进度与交付"],
];

export default function Home() {
  return (
    <main>
      <nav className="nav shell" aria-label="主导航">
        <Link className="brand" href="/" aria-label="小络助手首页">
          <img src={siteAssetPath("xiaoluo-logo.png")} alt="" />
          <span>小络助手</span>
        </Link>
        <div className="nav-links">
          <Link href="#value">产品价值</Link>
          <Link href="#workflow">办理流程</Link>
          <Link href="#case">真实案例</Link>
          <Link href="#scenes">业务场景</Link>
          <Link href="#integration">系统接入</Link>
        </div>
        <a className="nav-cta" href="/" target="_blank" rel="noreferrer">
          进入小络助手 <span aria-hidden="true">↗</span>
        </a>
      </nav>

      <section className="hero shell">
        <div className="hero-copy">
          <div className="eyebrow"><i />企业员工的智能业务办理入口</div>
          <h1>一句话，完成<br /><em>跨系统业务办理</em></h1>
          <p>
            员工直接说出要办的事。小络助手理解意图、匹配流程与规则，
            在对话中补齐信息、集中确认，并把结果带回当前会话。
          </p>
          <div className="hero-actions">
            <Link className="button primary" href="#case">查看真实办理流程 <span>↓</span></Link>
            <Link className="button secondary" href="#integration">了解接入方式 <span>→</span></Link>
          </div>
          <div className="trust-row" aria-label="产品特点">
            <span><b>✓</b>只需说清需求</span>
            <span><b>✓</b>关键内容确认</span>
            <span><b>✓</b>办理结果可追踪</span>
          </div>
        </div>

        <div className="hero-visual" aria-label="小络助手办理逻辑示意">
          <div className="visual-glow" />
          <article className="assistant-card">
            <header>
              <img src={siteAssetPath("xiaoluo-logo.png")} alt="" />
              <div><small>小络助手</small><strong>正在整理酒店申请</strong></div>
              <span>办理中</span>
            </header>
            <blockquote>“我要申请酒店住宿：北京朝阳区，1 人 1 晚，预算 1200 元。提交前让我确认。”</blockquote>
            <div className="assistant-steps">
              <div className="done"><i>✓</i><span><b>识别业务目标</b><small>酒店住宿申请</small></span></div>
              <div className="done"><i>✓</i><span><b>匹配字段与规则</b><small>已整理关键申请信息</small></span></div>
              <div className="active"><i>3</i><span><b>生成申请并确认</b><small>等待员工核对</small></span></div>
              <div><i>4</i><span><b>提交并返回结果</b><small>单号、状态、系统入口</small></span></div>
            </div>
            <footer><span>OA</span><span>HR</span><span>ERP</span><b>现有系统继续运行</b></footer>
          </article>
          <div className="float-note note-one"><b>业务规则已匹配</b><small>权限与流程保持不变</small></div>
          <div className="float-note note-two"><b>员工集中确认</b><small>关键动作由人决定</small></div>
        </div>
      </section>

      <section className="proof shell" aria-label="产品工作方式">
        <p>让复杂办理，像说一句话一样简单</p>
        <div><strong>理解</strong><span>员工意图与业务规则</span></div>
        <i />
        <div><strong>连接</strong><span>现有系统与数据</span></div>
        <i />
        <div><strong>执行</strong><span>确认、提交与查询</span></div>
      </section>

      <section className="section shell value" id="value">
        <div className="section-heading">
          <span>产品价值</span>
          <h2>员工不用适应系统，<br />系统开始理解员工</h2>
          <p>把“找入口、懂规则、填表单、反复修改”变成一段可以确认、执行和追踪的对话。</p>
        </div>
        <div className="value-layout">
          <article className="old-way">
            <div className="panel-label warm">传统方式</div>
            <h3>员工适应不同系统</h3>
            <ol>
              <li><span>1</span>切换不同系统</li>
              <li><span>2</span>熟悉各自规则</li>
              <li><span>3</span>逐项录入信息</li>
              <li><span>4</span>退回重复修改</li>
            </ol>
            <p><b>上手难度高</b><span>步骤分散，容易返工</span></p>
          </article>
          <div className="value-arrow" aria-hidden="true">→</div>
          <article className="new-way">
            <div className="panel-label">小络助手</div>
            <h3>员工只需说清需求</h3>
            <div className="value-steps">
              {valueSteps.map(([title, copy], index) => (
                <div key={title}>
                  <i className={index === 3 ? "green" : ""}>{index + 1}</i>
                  <span><b>{title}</b><small>{copy}</small></span>
                </div>
              ))}
            </div>
            <p><b>更易上手</b><span>一段对话完成业务办理</span></p>
          </article>
        </div>
      </section>

      <section className="workflow-section" id="workflow">
        <div className="shell">
          <div className="section-heading light">
            <span>使用体验</span>
            <h2>从一段对话，<br />形成可追踪的业务结果</h2>
            <p>原系统负责权威执行，小络助手负责组织需求、确认内容并把结果带回员工。</p>
          </div>
          <div className="workflow-grid">
            {workflow.map(([title, copy], index) => (
              <Fragment key={title}>
                <article>
                  <b>0{index + 1}</b>
                  <h3>{title}</h3>
                  <p>{copy}</p>
                </article>
                {index < workflow.length - 1 && <span className="workflow-connector" aria-hidden="true">→</span>}
              </Fragment>
            ))}
          </div>
          <div className="trace-row">
            <span>申请内容可查</span><span>提交记录可核</span><span>处理状态可追</span><span>原系统入口可达</span>
          </div>
        </div>
      </section>

      <section className="section shell case" id="case">
        <div className="section-heading split-heading">
          <div><span>生产环境演示</span><h2>酒店申请只是一个示例</h2></div>
          <p>同一套交互方式可以复用到更多 OA 与部门业务。下面展示员工发起、信息确认、提交和查询的完整过程。</p>
        </div>
        <article className="case-prompt">
          <div><small>员工输入示例</small><strong>把业务目标和关键条件一次说清</strong></div>
          <p>我要申请酒店住宿：北京朝阳区，1 人 1 晚，豪华房，预算 1200 元。请生成申请表，提交前让我确认。</p>
        </article>
        <div className="case-grid">
          <figure className="case-step">
            <div className="image-frame"><img src={siteAssetPath("start-input.jpg")} alt="小络助手生产环境对话输入框与酒店申请快捷入口" /></div>
            <figcaption><b>01</b><span><strong>员工发起</strong><small>直接输入目标，或点击“申请酒店”快捷入口</small></span></figcaption>
          </figure>
          <figure className="case-step">
            <div className="image-frame"><img src={siteAssetPath("application-form.jpg")} alt="小络助手根据员工需求生成酒店申请表" /></div>
            <figcaption><b>02</b><span><strong>申请信息自动生成</strong><small>自然语言需求转成完整业务字段</small></span></figcaption>
          </figure>
          <figure className="case-step">
            <div className="image-frame"><img src={siteAssetPath("confirm.jpg")} alt="员工提交酒店申请前集中确认关键信息" /></div>
            <figcaption><b>03</b><span><strong>员工核对并确认</strong><small>关键内容清晰可见，确认后再提交</small></span></figcaption>
          </figure>
          <figure className="case-step result-step">
            <div className="image-frame"><img src={siteAssetPath("result.jpg")} alt="酒店申请提交后返回单号、状态和原系统入口" /></div>
            <figcaption><b>04</b><span><strong>结果回到对话</strong><small>持续查询当前状态并直达原系统</small></span></figcaption>
          </figure>
        </div>
      </section>

      <section className="section shell scenes" id="scenes">
        <div className="section-heading split-heading">
          <div><span>业务场景</span><h2>同一种交互方式，<br />覆盖更多企业业务</h2></div>
          <p>员工始终通过对话表达需求，小络助手按照企业已有的流程、字段和规则完成信息组织与业务办理。</p>
        </div>
        <div className="scene-layout">
          <div>
            <h3>常用 OA 场景</h3>
            <div className="scene-list">
              {scenes.map(([title, copy]) => <article key={title}><i /> <b>{title}</b><span>{copy}</span></article>)}
            </div>
          </div>
          <div className="department-side">
            <h3>更多部门业务</h3>
            <div className="department-list">
              {departments.map(([title, copy]) => <article key={title}><small>{title}</small><b>{copy.split("、")[0]}</b><span>{copy}</span></article>)}
            </div>
            <h3 className="integration-title">可连接企业系统</h3>
            <div className="integration-list">
              {integrations.map(([title, copy]) => <span key={title}><b>{title}</b>{copy}</span>)}
            </div>
          </div>
        </div>
      </section>

      <section className="config-section" id="config">
        <div className="shell">
          <div className="section-heading">
            <span>业务配置</span>
            <h2>管理员配置业务，<br />员工即可通过对话办理</h2>
            <p>字段、规则和系统接口沉淀为可复用能力，同类事项可以持续稳定办理。</p>
          </div>
          <div className="config-flow">
            {[
              ["配置表单", "定义字段与必填项"],
              ["配置规则", "设置校验与业务规则"],
              ["接入系统", "绑定接口、权限与页面能力"],
              ["发布能力", "提供给员工持续使用"],
              ["对话办理", "员工直接说明需求"],
            ].map(([title, copy], index) => (
              <Fragment key={title}>
                <article className={index === 3 ? "green-card" : index === 2 ? "purple-card" : ""}>
                  <b>{title}</b><span>{copy}</span>
                </article>
                {index < 4 && <i className="config-connector" aria-hidden="true">→</i>}
              </Fragment>
            ))}
          </div>
          <div className="config-benefits">
            <span><i /> <b>管理员维护</b>业务变化时更新能力配置</span>
            <span><i /> <b>员工易上手</b>无需学习页面与字段</span>
            <span><i /> <b>能力可复用</b>同类事项持续稳定办理</span>
          </div>
        </div>
      </section>

      <section className="section shell integration" id="integration">
        <div className="section-heading split-heading">
          <div><span>无侵入接入</span><h2>现有系统保持不变，<br />老旧系统也能快速接入</h2></div>
          <p>小络助手通过独立工作台连接 OA、HR、ERP 等系统，不替换原页面、不迁移业务数据、不重建现有流程。</p>
        </div>
        <div className="integration-panels">
          <article className="keep-panel">
            <h3>现有系统继续运行</h3>
            <div><b>不替换系统</b><span>员工仍可按原方式使用</span></div>
            <div><b>不迁移数据</b><span>业务数据和单据留在原系统</span></div>
            <div><b>不重建流程</b><span>沿用账号、权限、校验和审批规则</span></div>
          </article>
          <article className="connect-panel">
            <h3>按系统条件选择接入方式</h3>
            <div><b>有标准接口</b><span>API、MCP 与企业连接器快速配置</span></div>
            <div><b>无标准接口</b><span>页面探测与 Browser Skill 适配老旧系统</span></div>
          </article>
        </div>
        <div className="delivery-row">
          <div><b>无侵入接入</b><span>不替换、不迁移、不重建</span></div>
          <div><b>兼容老旧系统</b><span>接口优先，页面能力兜底</span></div>
          <div><b>快速交付</b><span>低配置、可验证、可发布</span></div>
        </div>
      </section>

      <section className="cta-section">
        <div className="cta-mark"><img src={siteAssetPath("xiaoluo-logo.png")} alt="" /></div>
        <div>
          <span>从下一项员工业务开始</span>
          <h2>让员工直接说出要办的事</h2>
          <p>选择一个高频场景，接入现有系统，把复杂流程变成可确认、可执行、可追踪的一段对话。</p>
          <a className="button primary" href="/" target="_blank" rel="noreferrer">进入小络助手 <span>↗</span></a>
        </div>
      </section>

      <footer className="footer shell">
        <Link className="brand" href="/"><img src={siteAssetPath("xiaoluo-logo.png")} alt="" /><span>小络助手</span></Link>
        <p>面向企业员工的智能业务办理入口</p>
        <span>© 2026 小络助手</span>
      </footer>
    </main>
  );
}
