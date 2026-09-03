import http from "node:http";
import crypto from "node:crypto";
import { chromium } from "playwright";
import { RechargeService, RECHARGE_URL } from "./recharge.mjs";

const port = Number(process.env.CTYUN_BROWSER_WORKER_PORT || 18080);
const token = process.env.CTYUN_BROWSER_WORKER_TOKEN || "";
const headless = !["0", "false", "no"].includes(String(process.env.CTYUN_BROWSER_HEADLESS || "1").toLowerCase());
const idleMilliseconds = Math.max(60, Number(process.env.CTYUN_BROWSER_SESSION_IDLE_SECONDS || 600)) * 1000;
const cleanupMilliseconds = Math.max(30, Number(process.env.CTYUN_BROWSER_SESSION_CLEANUP_SECONDS || 120)) * 1000;
const sessions = new Map();
let browser;

const urls = {
  recharge: RECHARGE_URL,
  console: "https://console.ctyun.cn/compute/index/#/ecm/list",
  balance: "https://www.ctyun.cn/gw/account/giftcard/QueryBookSumm",
  owe: "https://www.ctyun.cn/v1/bcc/bill/QueryOwe",
  account: "https://www.ctyun.cn/v2/bcc/basicData/getCurrentInfo",
};

function json(res, status, value) {
  const body = JSON.stringify(value);
  res.writeHead(status, {"content-type":"application/json; charset=utf-8", "content-length":Buffer.byteLength(body)});
  res.end(body);
}
async function body(req) {
  const chunks=[]; let size=0;
  for await (const chunk of req) { size+=chunk.length; if(size>4*1024*1024) throw new Error("request_too_large"); chunks.push(chunk); }
  return chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : {};
}
function findDeep(value, keys) {
  if (Array.isArray(value)) for (const item of value) { const found=findDeep(item,keys); if(found!==undefined)return found; }
  else if (value && typeof value === "object") { for(const key of keys)if(value[key]!==undefined&&value[key]!==null)return value[key];for(const item of Object.values(value)){const found=findDeep(item,keys);if(found!==undefined)return found;} }
}
function amount(value){if(value===undefined||value===null||typeof value==="boolean")return null;const parsed=Number(String(value).replaceAll(",","").replace("¥","").trim());return Number.isFinite(parsed)?parsed:null;}
function totp(secret) {
  const normalized=String(secret||"").replaceAll(" ","").replaceAll("=","").toUpperCase(); if(!normalized)return "";
  const alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";let bits="";for(const char of normalized){const n=alphabet.indexOf(char);if(n<0)return "";bits+=n.toString(2).padStart(5,"0");}
  const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const digest=crypto.createHmac("sha1",Buffer.from(bytes)).update(counter).digest();const offset=digest.at(-1)&15;const code=(digest.readUInt32BE(offset)&0x7fffffff)%1000000;return String(code).padStart(6,"0");
}
async function getSession(account) {
  const id=String(account.id);let session=sessions.get(id);if(session)return session;
  browser ||= await chromium.launch({headless,args:["--no-sandbox","--disable-dev-shm-usage"]});
  const options={locale:"zh-CN",timezoneId:"Asia/Shanghai"};if(account.storage_state&&typeof account.storage_state==="object")options.storageState=account.storage_state;
  const context=await browser.newContext(options);const page=await context.newPage();session={context,page,lastUsed:Date.now()};sessions.set(id,session);return session;
}
async function visible(page,text){const loc=page.getByText(text,{exact:true});for(let i=0;i<await loc.count();i++)if(await loc.nth(i).isVisible())return loc.nth(i);return null;}
async function fill(page,placeholders,value){for(const p of placeholders){const loc=page.getByPlaceholder(p,{exact:true});if(await loc.count()===1&&await loc.isVisible()){await loc.fill(value);return true;}}return false;}
async function ensureLogin(account, target=urls.recharge) {
  const session=await getSession(account);const {page}=session;session.lastUsed=Date.now();await page.goto(target,{waitUntil:"domcontentloaded",timeout:60000});
  if(!/login|sso|passport/i.test(page.url()))return {status:"ready",message:"天翼云登录状态正常",session};
  const text=(await page.locator("body").innerText().catch(()=>""));if(/滑块验证|人机验证|图形验证码|短信验证码/.test(text))return {status:"manual_required",message:"官方页面要求人工安全验证",session};
  const tab=await visible(page,"账号登录");if(tab)await tab.click();
  const userOK=await fill(page,["登录名/邮箱","请输入登录名","请输入账号"],account.username||"");const passOK=await fill(page,["请输入密码","登录密码"],account.password||"");
  if(!userOK||!passOK)return {status:"manual_required",message:"登录表单无法自动识别",session};
  for(const checkbox of await page.locator('input[type="checkbox"]').all())if(!(await checkbox.isChecked())){await checkbox.check({force:true}).catch(()=>{});break;}
  const login=page.getByRole("button",{name:"登录",exact:true});if(await login.count())await login.last().click();
  await page.waitForTimeout(1200);const code=totp(account.totp_secret);if(code){if(30-Math.floor(Date.now()/1000)%30<=4)await page.waitForTimeout(5000);const ok=await fill(page,["请输入6位动态验证码","请输入动态验证码","请输入谷歌验证码","请输入Google验证码","请输入MFA验证码"],totp(account.totp_secret));if(ok){for(const name of ["登录","确认","验证","下一步"]){const btn=page.getByRole("button",{name,exact:true});if(await btn.count()&&await btn.last().isVisible()){await btn.last().click();break;}}}}
  await page.waitForTimeout(1500);if(/login|sso|passport/i.test(page.url()))return {status:"login_failed",message:"仍停留在天翼云登录页，请检查登录资料或安全验证",session};
  if(page.url()!==target)await page.goto(target,{waitUntil:"domcontentloaded",timeout:60000});return {status:"ready",message:"天翼云登录状态正常",session};
}
async function requestJSON(context,url,method="GET",data,referer=urls.recharge) {const cookies=await context.cookies(url);const csrf=cookies.find(cookie=>cookie.name==="csrftoken")?.value||"";const headers={accept:"application/json, text/plain, */*","accept-language":"zh-CN",origin:"https://www.ctyun.cn",referer,"x-requested-with":"XMLHttpRequest"};if(csrf)headers["x-csrftoken"]=csrf;const response=await context.request.fetch(url,{method,data,headers,timeout:45000});if([401,403].includes(response.status()))throw new Error("official_cookie_unauthorized");const text=await response.text();let parsed;try{parsed=JSON.parse(text)}catch{throw new Error(`official_not_json:${text.slice(0,200)}`)}return parsed;}
async function finance(account) {const ready=await ensureLogin(account);const state=await ready.session.context.storageState();if(ready.status!=="ready")return {...ready,session:undefined,storage_state:state,available:null,owe:null};const [b,o,a]=await Promise.all([requestJSON(ready.session.context,urls.balance),requestJSON(ready.session.context,urls.owe),requestJSON(ready.session.context,urls.account)]);const available=amount(findDeep(b,["cashPoints","availableBalance","availableAmount","cashBalance","availableCash"]));return {status:available===null?"finance_error":"ready",message:available===null?"官方余额接口没有返回可识别余额":"余额已从天翼云官方接口读取",available,owe:amount(findDeep(o,["realOwe","oweAmount","arrears","outstandingAmount"])),provider_account_id:String(findDeep(b,["accountId","accountID","account_id"])??findDeep(a,["accountId","accountID","account_id","tenantId","tenantID"])??""),storage_state:state,url:ready.session.page.url()};}

async function officialRequest(account,url,method,data,referer) {const ready=await ensureLogin(account);if(ready.status!=="ready")throw new Error(ready.message);return requestJSON(ready.session.context,url,method,data,referer);}
const recharge = new RechargeService(officialRequest);
async function rechargeResult(account, action) {const result=await action();const session=sessions.get(String(account.id));if(session)result.storage_state=await session.context.storageState();return result;}

const server=http.createServer(async(req,res)=>{try{
  if(req.url==="/healthz")return json(res,200,{status:"ok",service:"ctyun-browser-worker",sessions:sessions.size,accounts:[...sessions.keys()]});
  if(!token||req.headers.authorization!==`Bearer ${token}`)return json(res,401,{detail:"unauthorized"});
  const input=await body(req);const account=input.account||{};
  if(req.url==="/v1/finance"&&req.method==="POST")return json(res,200,await finance(account));
  if(req.url==="/v1/open"&&req.method==="POST"){const ready=await ensureLogin(account,input.target==="console"?urls.console:urls.recharge);return json(res,200,{status:ready.status,message:ready.message,url:ready.session.page.url(),storage_state:await ready.session.context.storageState()});}
  if(req.url==="/v1/recharge/order"&&req.method==="POST")return json(res,200,await rechargeResult(account,()=>recharge.order(account,input.amount,input.payment_method)));
  if(req.url==="/v1/recharge/payment"&&req.method==="POST")return json(res,200,await rechargeResult(account,()=>recharge.activate(account,input.payment_method)));
  if(req.url==="/v1/recharge/qr"&&req.method==="POST")return json(res,200,await recharge.qr(account));
  if(req.url==="/v1/recharge/refresh"&&req.method==="POST")return json(res,200,await rechargeResult(account,()=>recharge.refresh(account)));
  if(req.url==="/v1/recharge/status"&&req.method==="POST")return json(res,200,await rechargeResult(account,()=>recharge.status(account)));
  if(req.url==="/v1/prewarm"&&req.method==="POST"){const ready=await ensureLogin(account,urls.recharge);return json(res,200,{status:ready.status,message:ready.message,storage_state:await ready.session.context.storageState()});}
  if(req.url==="/v1/close"&&req.method==="POST"){recharge.close(account.id);const session=sessions.get(String(account.id));if(session){await session.context.close();sessions.delete(String(account.id));}return json(res,200,{ok:true});}
  return json(res,404,{detail:"not_found"});
}catch(error){return json(res,502,{detail:String(error?.message||error)});}});
server.listen(port,"127.0.0.1",()=>process.stdout.write(`ctyun browser worker listening on 127.0.0.1:${port}\n`));
const cleanupTimer=setInterval(async()=>{const cutoff=Date.now()-idleMilliseconds;for(const [id,session] of sessions)if(session.lastUsed<cutoff){recharge.close(id);await session.context.close().catch(()=>{});sessions.delete(id);}},cleanupMilliseconds);cleanupTimer.unref();
async function shutdown(){clearInterval(cleanupTimer);server.close();for(const session of sessions.values())await session.context.close().catch(()=>{});if(browser)await browser.close().catch(()=>{});process.exit(0);}process.on("SIGTERM",shutdown);process.on("SIGINT",shutdown);
