"""Standalone desktop UI: python app.py. No web server or third-party packages."""
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from client import (ClientError,DEFAULT_BASE_URL,DEFAULT_MODEL,DEFAULT_PROMPT,
                    build_request,endpoint,sample_images,send_images)


class App:
    def __init__(self,root):
        self.root=root; self.images=[]; self.busy=False; self.events=queue.Queue()
        root.title('小怪 · 千问图片传输工具');root.geometry('1000x800');root.minsize(750,650)
        frame=ttk.Frame(root,padding=18);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='图片 → 阿里云百炼千问',font=('Microsoft YaHei',18,'bold')).pack(anchor='w')
        ttk.Label(frame,text='只传输选定图片和提示词，不读取真值，不做轨迹反演或参数调控。').pack(anchor='w',pady=(3,12))
        config=ttk.LabelFrame(frame,text='连接配置（API Key仅在当前进程内使用）',padding=10);config.pack(fill='x')
        self.key=tk.StringVar(value=os.environ.get('MODEL_API_KEY',''))
        self.base=tk.StringVar(value=os.environ.get('MODEL_BASE_URL',DEFAULT_BASE_URL))
        self.model=tk.StringVar(value=os.environ.get('MODEL_NAME',DEFAULT_MODEL))
        for row,(label,var,secret) in enumerate([('API Key',self.key,True),('Base URL',self.base,False),('视觉模型名称',self.model,False)]):
            ttk.Label(config,text=label).grid(row=row,column=0,sticky='w',padx=(0,12),pady=4)
            ttk.Entry(config,textvariable=var,show='*' if secret else '').grid(row=row,column=1,sticky='ew',pady=4)
        config.columnconfigure(1,weight=1)
        buttons=ttk.Frame(frame);buttons.pack(fill='x',pady=(12,6))
        ttk.Button(buttons,text='选择图片（可多选）',command=self.choose).pack(side='left')
        ttk.Button(buttons,text='选择实验样本文件夹',command=self.choose_sample).pack(side='left',padx=8)
        ttk.Button(buttons,text='清空',command=self.clear).pack(side='left')
        self.listbox=tk.Listbox(frame,height=4);self.listbox.pack(fill='x')
        ttk.Label(frame,text='提示词').pack(anchor='w',pady=(10,4))
        self.prompt=tk.Text(frame,height=5,wrap='word',font=('Microsoft YaHei',10));self.prompt.pack(fill='x');self.prompt.insert('1.0',DEFAULT_PROMPT)
        self.consent=tk.BooleanVar(value=False)
        ttk.Checkbutton(frame,text='我确认：将以上图片和提示词发送至阿里云百炼，可能产生API费用。',variable=self.consent).pack(anchor='w',pady=10)
        action=ttk.Frame(frame);action.pack(fill='x')
        self.send_button=ttk.Button(action,text='发送给千问',command=self.send);self.send_button.pack(side='left')
        self.status=tk.StringVar(value='未发送。请配置API Key，选择图片并勾选确认。')
        ttk.Label(action,textvariable=self.status,wraplength=730).pack(side='left',padx=12)
        ttk.Label(frame,text='模型返回（原文；不代表已经生成轨迹）').pack(anchor='w',pady=(12,4))
        output=ttk.Frame(frame);output.pack(fill='both',expand=True)
        self.answer=tk.Text(output,wrap='word',font=('Microsoft YaHei',10),state='disabled');self.answer.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(output,command=self.answer.yview);scroll.pack(side='right',fill='y');self.answer.configure(yscrollcommand=scroll.set)
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(150,self.poll)

    def show_images(self,paths):
        self.images=list(paths);self.listbox.delete(0,'end')
        for path in self.images:self.listbox.insert('end',str(path))
        self.consent.set(False)

    def choose(self):
        if self.busy:return
        paths=filedialog.askopenfilenames(title='选择声呐图片',filetypes=[('图片','*.png *.jpg *.jpeg *.webp')])
        if paths:self.show_images(paths)

    def choose_sample(self):
        if self.busy:return
        directory=filedialog.askdirectory(title='选择sample_0001等样本目录')
        if directory:
            try:self.show_images(sample_images(directory))
            except ClientError as exc:messagebox.showerror('无法读取',str(exc))

    def clear(self):
        if not self.busy:self.show_images([])

    def send(self):
        if self.busy:return
        if not self.consent.get():
            messagebox.showwarning('尚未确认','请勾选云端传输与费用确认。');return
        images=list(self.images);prompt=self.prompt.get('1.0','end').strip()
        model=self.model.get().strip();base=self.base.get().strip();key=self.key.get().strip()
        try:
            endpoint(base);build_request(images,prompt,model)
            if not key:raise ClientError('请填写API Key或配置MODEL_API_KEY。')
        except (ClientError,OSError,ValueError) as exc:
            messagebox.showerror('参数错误',str(exc));return
        self.busy=True;self.send_button.configure(state='disabled')
        self.status.set('正在发送并等待模型，本地不设等待时限；不会自动重试。')
        def worker():
            try:self.events.put(('ok',send_images(images,prompt,model,base,key)))
            except Exception as exc:
                # Do not dump exceptions that might include request headers.
                message=str(exc) if isinstance(exc,(ClientError,OSError,ValueError)) else '发生未预期错误；请检查配置。'
                self.events.put(('error',message.replace(key,'[REDACTED]')))
        threading.Thread(target=worker,daemon=True).start()

    def poll(self):
        try:
            kind,data=self.events.get_nowait();self.busy=False;self.send_button.configure(state='normal')
            if kind=='ok':
                self.answer.configure(state='normal');self.answer.delete('1.0','end');self.answer.insert('1.0',data['answer']);self.answer.configure(state='disabled')
                self.status.set('已完成，结果保存：'+data['output_dir'])
            else:self.status.set(data);messagebox.showerror('调用失败',data)
        except queue.Empty:pass
        self.root.after(150,self.poll)

    def close(self):
        if self.busy and not messagebox.askyesno('请求进行中','关闭窗口不能撤回已发送的请求，可能仍产生费用。是否关闭？'):return
        self.root.destroy()


if __name__=='__main__':
    App(tk.Tk()).root.mainloop()
