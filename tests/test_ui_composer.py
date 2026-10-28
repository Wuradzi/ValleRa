"""Offline UI behavior checks; no server, microphone or model calls."""
import shutil
import subprocess
import unittest
from pathlib import Path


class UIComposerTests(unittest.TestCase):
    def test_raven_layout_preserves_interactive_hooks(self):
        from html.parser import HTMLParser
        from collections import Counter

        class Elements(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids = []

            def handle_starttag(self, tag, attrs):
                self.ids.extend(value for name, value in attrs if name == 'id')

        root = Path(__file__).resolve().parents[1]
        html = (root / 'web_ui/index.html').read_text(encoding='utf-8')
        parser = Elements()
        parser.feed(html)
        counts = Counter(parser.ids)
        self.assertTrue(all(count == 1 for count in counts.values()))
        for hook in ('message', 'send', 'microphone', 'history-toggle',
                     'confirmation', 'accept', 'reject', 'agent-task',
                     'cancel-task', 'about-dialog'):
            self.assertEqual(counts[hook], 1, hook)
        self.assertIn('class="raven-eye"', html)
        self.assertIn('class="raven-ring"', html)
        self.assertNotIn('face-outline', html)
        self.assertNotIn('mouth-wave', html)
        self.assertNotIn('id="stop"', html)
        self.assertIn('class="stop-icon"', html)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for browser-script checks')
    def test_edit_and_stop_preserve_confirmation_and_require_explicit_send(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const nodes = new Map();
function element() { return {value:'', checked:true, dataset:{}, style:{}, scrollHeight:38,
  scrollTop:0, clientHeight:38, children:[],
  classList:{toggle(){},add(){},remove(){}}, setAttribute(){},
  addEventListener(){}, replaceChildren(){}, focus(){}, querySelector(){return element();}}; }
const context = {assert, console, setTimeout, clearTimeout,
  matchMedia:()=>({matches:false,addEventListener(){}}),
  localStorage:{getItem:()=>null}, sessionStorage:{getItem:()=>null},
  location:{hash:'',pathname:'/'}, history:{replaceState(){}}, URLSearchParams,
  document:{body:element(), hidden:false, addEventListener(){},
    querySelectorAll:()=>[], getElementById(id){
      if(!nodes.has(id)) nodes.set(id,element()); return nodes.get(id);
    }}
};
vm.createContext(context);
const source=fs.readFileSync('web_ui/app.js','utf8');
vm.runInContext(source.replace(/poll\(\);\s*$/, ''), context);
vm.runInContext(`
(async()=>{
  connected=true;
  let calls=[];
  state={tts_playback:true, microphone_available:true, microphone:true};
  $('message').value='чернетка';
  controls();
  assert.equal($('send').dataset.mode,'stop');
  assert.equal($('send').disabled,false);
  assert.equal($('microphone').disabled,false);
  action=async(name)=>{calls.push(name); return true;};
  await submitComposer();
  assert.deepEqual(calls,['stop']);
  assert.equal($('message').value,'чернетка');
  state={tts_playback:false,tts_busy:false};
  $('message').value=''; controls();
  assert.equal($('send').dataset.mode,'send');
  assert.equal($('send').disabled,true);
  state={tts_busy:true}; controls();
  assert.equal($('send').dataset.mode,'stop');
  connected=false; controls();
  assert.equal($('send').disabled,true);
  connected=true; state={}; calls=[];
  renderGlow(true, {sequence:1,strength:4,source:'pcm',samples:Array(32).fill(0),age_ms:0});
  assert.equal(document.body.dataset.glow,'4');
  renderGlow(false);
  assert.equal(document.body.dataset.glow,'0');
  action=async(name,fields)=>{calls.push({name,fields}); return true;};
  editTranscript('почута репліка');
  assert.equal($('message').style.height,'38px');
  $('message').scrollHeight=240; resizeComposer();
  assert.equal($('message').style.height,'144px');
  assert.equal($('message').value,'почута репліка');
  assert.equal(calls.length,0); // editing never submits or executes
  editTranscript('не перезаписувати');
  assert.equal($('message').value,'почута репліка');
  confirmationId='new-request';
  await send($('message').value);
  assert.equal(calls[0].fields.request_id,null); // never binds old speech to new approval
  assert.equal(calls[0].name,'message');
  editTranscript('так');
  assert.equal($('message').value,''); // disabled during confirmation
  confirmationId=null; connected=false;
  editTranscript('offline');
  assert.equal($('message').value,'');
  connected=true;
  let release;
  calls=[];
  action=(name)=>{calls.push(name); return new Promise(r=>{release=r;});};
  const pending=stopResponse();
  await stopResponse();
  assert.equal(calls.length,1);
  assert.equal(calls[0],'stop');
  assert.equal(stopping,true);
  release(true); await pending;
  assert.equal(stopping,false);
  const blocks=Array.from({length:8},(_,i)=>({classList:{
    contains:name=>name==='user' && i%2===0,
    toggle:(name,value)=>{blocks[i].visible=value;}
  }}));
  messages.children=blocks;
  followingLatest=true; updateCompactConversation();
  assert.deepEqual(blocks.map(b=>b.visible),[false,false,true,true,true,true,true,true]);
  followingLatest=false;
  blocks[0].visible=true; updateCompactConversation();
  assert.equal(blocks[0].visible,true); // reader's viewport is not trimmed
  messages.scrollHeight=900; messages.clientHeight=200; messages.scrollTop=100;
  assert.equal(atLatest(),false);
  jumpToLatest();
  assert.equal(messages.scrollTop,900);
  assert.equal($('jump-latest').hidden,true);
  const task={id:'search',kind:'file_search',title:'Пошук',active:true,status:'running',detail:'12',cancellation:'Зупинка між кроками',steps:[]};
  state={task}; renderTask(task); controls();
  assert.equal($('agent-task').hidden,false);
  assert.equal($('cancel-task').disabled,false);
  task.status='cancelling'; task.cancel_requested=true; renderTask(task); controls();
  assert.equal($('cancel-task').disabled,true);
  task.status='cancelled'; task.active=false; renderTask(task);
  assert.equal($('task-status').textContent,'Скасовано');
  connected=false;
  renderTask(task); controls();
  assert.equal($('cancel-task').disabled,true);
  assert.ok($('task-status').textContent.startsWith('Останній відомий стан:'));
})().catch(error=>{console.error(error); process.exitCode=1;});
`, Object.assign(context,{process}));
'''
        result = subprocess.run([shutil.which('node'), '-e', script], cwd=root,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
