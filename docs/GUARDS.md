# 文字守卫的合约

`verification\test_text_guard.py` 里的静态守卫 G1（`src` 里的中文字面量、核心模块的文字用法）和 G5（界面上显示的值必须是当前语言的文字）只检查下面列出的写法。它们在架构里的位置见 `ARCHITECTURE.md` 第 6 节。

- 合约就是 `verification\i18n_tool.py` 的 `CONTRACT`：每个来源（`S` 写死在源码里的文字，`N` 不算来源，`O` 事件值）、每种传递方式（`F1`…`F11`）、每种显示位置及其写法各一个编号，并写明它约束哪个守卫。
- `verification\guard_contract.py` 为每个编号生成必须报出和必须不报的样例，全部要得出合约规定的结果；`guard_contract.MUTANTS` 每次去掉扫描器的一处行为，对应编号的样例必须出错（`test_removing_each_behaviour_fails_a_case`）。
- 能说的只有这些：列出的写法 G1 / G5 都检查，每个编号都有样例且结果正确。不能说"任何 Python 写法都在合约内"：合约以外的写法由运行时守卫 G2（`run_tests.py` 给每个测试文件开 `YTDLP_DESKTOP_MSG_GUARD`）和英文界面测试在测试走到的路径上兜底。
- 下面的清单必须与 `CONTRACT` 完全相同（`test_the_contract_list_is_the_published_spec`）：在 `CONTRACT` 里加、删编号时同步改这里。

<!-- guard contract ids (A16.7) -->
```text
S1 allow-listed literal
S2 Chinese literal
S3 class text
S3 module text
S4 other module
S5 enum source
S6 zh template
N msg()
N raw()
N number
N show()
N show(raw())
N t()
O G1f composition
O event value
F1 alias chain
F1 annotated assignment
F1 assignment
F1 augmented assignment
F1 comprehension target
F1 direct value
F1 for target
F1 global
F1 nonlocal
F1 parameter rebound
F1 rebinding
F1 walrus
F1 with as
F2 from import
F2 module name
F2 module.NAME
F3 annotated attribute
F3 attribute mutation
F3 augmented attribute
F3 class attribute
F3 class name attribute
F3 cls attribute
F3 getattr
F3 getattr default
F3 property
F3 self attribute
F3 setattr
F3 tuple-target attribute
F3 cross-module attribute
F4 conditional
F4 or / and
F5 ** spread
F5 add
F5 append
F5 dict comprehension
F5 dict comprehension keys
F5 dict get
F5 dict item
F5 dict iteration
F5 extend
F5 generator
F5 get default
F5 insert
F5 item assignment
F5 item augmented
F5 items()
F5 keys()
F5 list comprehension
F5 list slice
F5 nested selection
F5 neutral-named item
F5 pop
F5 pop default
F5 popitem()
F5 set comprehension
F5 set display
F5 setdefault
F5 starred element
F5 tuple index
F5 update(k=)
F5 update(mapping)
F5 values()
F6 enumerate
F6 items() unpacking
F6 nested unpacking
F6 starred unpacking
F6 unpacking
F6 zip
F7 copy.copy
F7 dict(k=)
F7 dict(mapping)
F7 dict(pairs)
F7 filter
F7 frozenset()
F7 iter()
F7 list()
F7 map
F7 max()
F7 max() several
F7 min()
F7 min() default
F7 next()
F7 next() default
F7 reversed
F7 set()
F7 sorted
F7 tuple()
F8 %
F8 *
F8 +
F8 .format
F8 .format_map
F8 .join
F8 .join receiver
F8 center inserted
F8 f-string
F8 f-string conversion
F8 format()
F8 re.sub
F8 replace inserted
F8 str method
F8 str method unbound
F8 str()
F8 text slice
F8 textwrap
F9 alias of a function
F9 classmethod
F9 dict of lambdas
F9 from-import function
F9 function return
F9 getattr method
F9 lambda
F9 lambda name
F9 method return
F9 nested function
F9 other module function
F9 returned parameter
F9 staticmethod
F9 yield
F10 depth 2
F10 depth 3
F10 keyword
F10 parameter default
F10 positional
F11 Path().name
F11 os.fspath
F11 urls.display
F11 show()
A15.4 G1b ['title'] =
A15.4 G1b clauses(parts)
A15.4 G1b dict 'title'
A15.4 G1b emit()
A15.4 G1b keyword title=
A15.4 G1b msg param
A15.4 G1b raise
A15.4 G1b raw(text)
A15.4 G1b store writer
A15.4 G1b store writer record
A15.4 append_log
A15.4 chip set
A15.4 configure
A15.4 constructor
A15.4 filedialog
A15.4 fill
A15.4 heading
A15.4 library filetext t()
A15.4 menu
A15.4 messagebox
A15.4 set_empty
A15.4 show()
A15.4 sink method
A15.4 t()
A15.4 t() mapping
A15.4 textbox insert
A15.4 theme
A15.4 theme columns
A15.4 theme unlabelled
A15.4 title
A15.4 value sink method
A15.4 values
A16.1 append_log after
A16.1 append_log callee alias
A16.1 append_log getattr
A16.1 append_log line=
A16.1 append_log partial
A16.1 chip set keyword
A16.1 chip set local
A16.1 chip set row item
A16.1 chip set self binding
A16.1 config text=
A16.1 configure **mapping display
A16.1 configure **mapping name
A16.1 configure callee alias
A16.1 configure cnf mapping
A16.1 configure partial
A16.1 configure placeholder=
A16.1 configure placeholder_text=
A16.1 constructor **kw wrapper
A16.1 constructor **mapping
A16.1 constructor customtkinter
A16.1 constructor from-import
A16.1 constructor import alias
A16.1 constructor placeholder=
A16.1 constructor placeholder_text=
A16.1 constructor tk
A16.1 constructor tkinter
A16.1 constructor ttk
A16.1 constructor values=
A16.1 error_text()
A16.1 error_text(error=)
A16.1 filedialog askdirectory
A16.1 filedialog askopenfile
A16.1 filedialog askopenfilename
A16.1 filedialog askopenfilenames
A16.1 filedialog askopenfiles
A16.1 filedialog asksaveasfile
A16.1 filedialog asksaveasfilename
A16.1 filedialog filetypes=
A16.1 filedialog initialfile=
A16.1 fill rows=
A16.1 heading **mapping
A16.1 item values=
A16.1 menu add_cascade
A16.1 menu add_checkbutton
A16.1 menu add_command
A16.1 menu add_radiobutton
A16.1 menu entryconfig
A16.1 menu entryconfigure
A16.1 menu insert_cascade
A16.1 menu insert_checkbutton
A16.1 menu insert_command
A16.1 menu insert_radiobutton
A16.1 messagebox askokcancel
A16.1 messagebox askquestion
A16.1 messagebox askretrycancel
A16.1 messagebox askyesno
A16.1 messagebox askyesnocancel
A16.1 messagebox callee alias
A16.1 messagebox detail=
A16.1 messagebox dotted
A16.1 messagebox from-import
A16.1 messagebox message=
A16.1 messagebox module alias
A16.1 messagebox showerror
A16.1 messagebox showinfo
A16.1 messagebox showwarning
A16.1 messagebox starred
A16.1 messagebox title
A16.1 messagebox title=
A16.1 set_empty text=
A16.1 show at a strict sink by keyword
A16.1 show bare
A16.1 show callee alias
A16.1 show(value=)
A16.1 sink method ** argument
A16.1 sink method **param
A16.1 sink method *param
A16.1 sink method Thread args
A16.1 sink method Thread kwargs
A16.1 sink method after
A16.1 sink method after_idle
A16.1 sink method callee alias
A16.1 sink method getattr
A16.1 sink method keyword
A16.1 sink method keyword-only
A16.1 sink method lambda
A16.1 sink method module function
A16.1 sink method nested
A16.1 sink method partial
A16.1 sink method partial keyword
A16.1 sink method starred argument
A16.1 t() mapping name
A16.1 textbox insert binding
A16.1 textbox insert chars=
A16.1 textbox insert ctk binding
A16.1 textbox insert text=
A16.1 theme **mapping
A16.1 theme button text
A16.1 theme button text keyword
A16.1 theme callee alias
A16.1 theme checkbox text
A16.1 theme checkbox text keyword
A16.1 theme chip text
A16.1 theme chip text keyword
A16.1 theme empty_state text
A16.1 theme empty_state text keyword
A16.1 theme entry placeholder
A16.1 theme entry placeholder keyword
A16.1 theme fit text
A16.1 theme fit text keyword
A16.1 theme from-import
A16.1 theme label text
A16.1 theme label text keyword
A16.1 theme module alias
A16.1 theme option_menu labels
A16.1 theme option_menu labels keyword
A16.1 theme option_menu values
A16.1 theme option_menu values keyword
A16.1 theme segmented labels
A16.1 theme segmented labels keyword
A16.1 theme switch text
A16.1 theme switch text keyword
A16.1 theme table columns
A16.1 theme table columns keyword
A16.1 theme table empty
A16.1 theme table empty keyword
A16.1 theme tabs labels keyword
A16.1 theme tabs names
A16.1 theme tabs names keyword
A16.1 title string=
A16.1 value sink method **param
A16.1 value sink method keyword
A16.1 value sink method lambda
A16.1 value sink method through show
A16.1 wm_title
A16.4 .reason = in a tuple target
A16.4 .title =
A16.4 ['title'] +=
A16.4 cut(value)
A16.4 emit callee alias
A16.4 emit keyword
A16.4 emit payload
A16.4 finish_subscription
A16.4 keyword TEXT_KEYS parameter
A16.4 masked(value)
A16.4 message_of(error)
A16.4 other module parameter slot
A16.4 positional TEXT_KEYS parameter
A16.4 raw(text=)
A16.4 say()
A16.4 self.emit()
A16.4 setdefault('title')
A16.4 store writer keyword
A16.4 stripped(value)
A16.4 unfinished.merge
A16.4 unfinished.merge record
A16.4 unfinished.save record
```
