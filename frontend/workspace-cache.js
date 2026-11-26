'use strict';
// Browser-local structured storage: File/Blob values never leave this origin.
(()=>{
  let database,queue=Promise.resolve(),fileQueue=Promise.resolve();
  function open(){
    if(!globalThis.indexedDB)return Promise.reject(new Error('浏览器不支持IndexedDB'));
    if(!database)database=new Promise((resolve,reject)=>{
      const request=indexedDB.open('sonar-workspace-v1',2);let blocked=false;
      const timer=setTimeout(()=>{blocked=true;database=null;reject(new Error('文件库连接超时，请关闭其他旧工作台标签页后刷新重试；请勿清除浏览器数据，原文件未删除。'));},10000);
      request.onupgradeneeded=()=>{
        const db=request.result;
        if(!db.objectStoreNames.contains('snapshots'))db.createObjectStore('snapshots');
        if(!db.objectStoreNames.contains('point_files')){
          const files=db.createObjectStore('point_files');
          // One upgrade transaction copies existing files and tombstones. Keep
          // the v1 records as a backup; file reads never fall back after upgrade.
          const cursor=request.transaction.objectStore('snapshots').openCursor();
          cursor.onsuccess=()=>{
            const row=cursor.result;if(!row)return;const key=String(row.key);
            if(key.startsWith('point-file-deleted-')&&row.value===true)files.put(true,'deleted:'+key.slice('point-file-deleted-'.length));
            else if(key.startsWith('point-file-')&&row.value&&typeof row.value==='object'){
              const id=key.slice('point-file-'.length);files.put({...row.value,id},'file:'+id);
            }
            row.continue();
          };
        }
      };
      request.onsuccess=()=>{
        clearTimeout(timer);const db=request.result;if(blocked){db.close();return;}
        db.onversionchange=()=>{db.close();database=null;};resolve(db);
      };
      request.onerror=()=>{clearTimeout(timer);database=null;reject(request.error);};
      request.onblocked=()=>{clearTimeout(timer);blocked=true;database=null;reject(new Error('文件库升级被旧标签页占用，请关闭或刷新其他工作台标签页后重试；原数据未删除。'));};
    });
    return database;
  }
  async function transaction(storeName,mode,operation){
    const db=await open();
    return new Promise((resolve,reject)=>{
      const tx=db.transaction(storeName,mode);let result;
      const timer=setTimeout(()=>{try{tx.abort();}catch{}reject(new Error('本地文件库读写超时；未清除原数据，请关闭其他工作台标签页后刷新重试。'));},15000);
      try{operation(tx.objectStore(storeName),value=>{result=value;});}
      catch(error){clearTimeout(timer);tx.abort();reject(error);return;}
      tx.oncomplete=()=>{clearTimeout(timer);resolve(result);};
      tx.onerror=tx.onabort=()=>{clearTimeout(timer);reject(tx.error||new Error('浏览器本地保存失败'));};
    });
  }
  function write(operation){
    const next=queue.catch(()=>{}).then(()=>transaction('snapshots','readwrite',operation));
    queue=next;return next;
  }
  globalThis.WorkspaceCache={
    get:async key=>{await queue.catch(()=>{});return transaction('snapshots','readonly',(store,done)=>{const request=store.get(key);request.onsuccess=()=>done(request.result);});},
    set:(key,value)=>write(store=>store.put(value,key)),
    remove:key=>write(store=>store.delete(key))
  };
  function fileWrite(operation){
    const next=fileQueue.catch(()=>{}).then(()=>transaction('point_files','readwrite',operation));
    fileQueue=next;return next;
  }
  // Files live in their own store, not in conversation snapshots or their index.
  // No mutable file index is needed, avoiding lost entries on concurrent imports.
  globalThis.PointFiles={
    put(data,{manual=false}={}){
      const id=data.source_result_id||data.batch_id||crypto.randomUUID();
      return fileWrite((store,done)=>{
        const request=store.get('deleted:'+id);request.onsuccess=()=>{
          if(request.result&&!manual){done(null);return;}
          if(manual)store.delete('deleted:'+id);
          store.put({id,name:data.file_name||'观测点_'+id+'.json',data,created_at:new Date().toISOString()},'file:'+id);done(id);
        };
      });
    },
    async list(){
      await fileQueue.catch(()=>{});
      return transaction('point_files','readonly',(store,done)=>{
        const files=[],deleted=new Set(),request=store.openCursor();
        request.onsuccess=()=>{
          const row=request.result;
          if(!row){done(files.filter(f=>!deleted.has(f.id)).sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||''))||a.id.localeCompare(b.id)));return;}
          const key=String(row.key);if(key.startsWith('deleted:')&&row.value)deleted.add(key.slice(8));
          else if(key.startsWith('file:'))files.push(row.value);row.continue();
        };
      });
    },
    remove:id=>fileWrite(store=>{store.put(true,'deleted:'+id);store.delete('file:'+id);}),
    download(file){const url=URL.createObjectURL(new Blob([JSON.stringify(file.data,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=file.name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  };
})();
