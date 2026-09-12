/* 控制台脚本语法体检：抽出 console.html 里的 <script> 块，用 new Function 编译。
   不执行（没有 DOM），只保证没有语法错误 —— 这是 HTML 里 JS 唯一能自动化的一层。
   用法：node scripts/console_js_check.js [path-to-console.html]
*/
const fs = require('fs');
const path = require('path');

const file = process.argv[2]
  || path.join(__dirname, '..', 'packages', 'a2n-server', 'src', 'a2n_server', 'web', 'console.html');
const html = fs.readFileSync(file, 'utf8');

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

// 顺手体检：模板字符串里出现未转义的双引号拼进 HTML 属性，会被浏览器截断属性
const attrHazards = [...html.matchAll(/on\w+="[^"]*\$\{[^}]*\}"[^>]*\$/g)];
if (bad) process.exit(1);
console.log(`✓ ${path.relative(process.cwd(), file)} JS 语法全部通过`);
