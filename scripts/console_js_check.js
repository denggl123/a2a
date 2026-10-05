/* 控制台脚本语法体检：抽出 console.html 里的 <script> 块，用 new Function 编译。
   不执行（没有 DOM），只保证没有语法错误 —— 这是 HTML 里 JS 唯一能自动化的一层。
   同时把**本地外链** <script src>（如 /console/coordination.js，Web 根 = web/）
   也编译一遍：内联块体检不到它们，而页面能不能起来全靠它们。
   用法：node scripts/console_js_check.js [path-to-console.html]
*/
const fs = require('fs');
const path = require('path');

const file = process.argv[2]
  || path.join(__dirname, '..', 'packages', 'a2n-sdk', 'src', 'a2n_sdk', 'web', 'runtime.html');
const html = fs.readFileSync(file, 'utf8');
const webRoot = path.dirname(file);          // 页面对外链的 Web 根（/console/x.js → web/x.js）

const blocks = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map(m => m[1]);
if (!blocks.length) {
  console.error(`✗ ${file} 里没找到 <script> 块`);
  process.exit(1);
}

let bad = 0;
blocks.forEach((code, i) => {
  try {
    new Function(code);            // eslint-disable-line no-new-func
    console.log(`✓ script[${i}] 语法通过（${code.length} 字符）`);
  } catch (e) {
    bad++;
    console.error(`✗ script[${i}] 语法错误：${e.message}`);
  }
});

// 本地外链脚本：同样只编译不执行。远端 CDN 跳过（不在本仓）。
function checkExternal(src, target) {
  if (!fs.existsSync(target)) {
    bad++;
    console.error(`✗ 本地外链 ${src} 找不到文件：${path.relative(process.cwd(), target)}`);
    return 0;
  }
  const code = fs.readFileSync(target, 'utf8');
  try {
    new Function(code);            // eslint-disable-line no-new-func
    console.log(`✓ ${src} 语法通过（${code.length} 字符）`);
    return 1;
  } catch (e) {
    bad++;
    console.error(`✗ ${src} 语法错误：${e.message}`);
    return 1;
  }
}
const srcs = [...html.matchAll(/<script\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/gi)].map(m => m[1]);
let externals = 0;
for (const src of srcs) {
  if (/^(https?:)?\/\//i.test(src)) continue;
  // 页面对外只暴露 /console/*，根绝对路径映射到 web 根下的同名文件；相对路径按页面目录解析。
  externals += checkExternal(src, src.startsWith('/')
    ? path.join(webRoot, src.replace(/^\/console\//, '').replace(/^\//, ''))
    : path.resolve(webRoot, src));
}

// 顺手体检：内联事件属性里用 ${} 插值拼进 HTML 属性，会被浏览器截断属性。
// 只报警不判失败 —— 它是"值得看一眼"，不是"一定坏"。
// 要求 on* 前是空白/引号/尖括号，否则 `data-...-versions=` 这种"以 ons= 结尾"
// 的属性名会被误判成 on* 事件（踩过：误报 2 处 data-fb-* 属性）。
const attrHazards = [...html.matchAll(/(?<=[\s"'<])on\w+="[^"]*\$\{[^}]*\}"/g)];
if (attrHazards.length) {
  console.warn(`⚠ 发现 ${attrHazards.length} 处内联 on*="" 里带 \${} 插值，可能截断属性：`);
  attrHazards.slice(0, 5).forEach(m => console.warn('   ' + m[0].slice(0, 120)));
}

if (bad) process.exit(1);
console.log(`✓ ${path.relative(process.cwd(), file)} JS 语法全部通过` +
            (externals ? `（含 ${externals} 个本地外链脚本）` : ''));
