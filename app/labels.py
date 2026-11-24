TABS = {'intake':'原件采信','registry':'槽位版本','custody':'查验占位','exposure':'门区暴露','capacity':'余量对账',
        'history':'批次留档','certificate':'冷库证据'}
KINDS = {'closed':'闭门','warm':'温超限','cooled':'温合格','missing_temp':'温度缺证','missing_slot':'槽位缺证','missing_door':'门态缺证'}
CODES = {'WIDTH':'列数不符','MISSING':'必填字段缺失','MOVE_FIELDS':'动作字段不符','TIME':'时间无效',
         'SIGNAL':'门信号无效','EVENT_CONFLICT':'同号日志矛盾','DUP_PACKET':'重复收件','SLOT_AMBIGUOUS':'槽位版本冲突',
         'SLOT_UNKNOWN':'缺少槽位证据','CAPACITY_EXCEEDED':'调入超出槽位容量','INVALID_LIFECYCLE':'无效入出库动作',
         'SIMULTANEOUS':'同刻动作冲突','LOT_UNKNOWN':'未知货批','TICKET_MISMATCH':'查验单不匹配','ALREADY_FROZEN':'货批已冻结',
         'FROZEN_OUT':'冻结件不得出库','DOOR_BOUNCE':'门信号抖动','DOOR_CONFLICT':'同刻门态矛盾',
         'TEMP_INVALID':'温度读数无效','TEMP_CONFLICT':'同刻温度矛盾',
         'OUT_DECLARED':'出库申报待闸口','WRONGFUL_EXIT':'误出闸','GATE_UNREPORTED':'未申报出闸',
         'GATE_DUPLICATE':'闸口回执重复','EXIT_DECLARED':'仅申报未出闸','DUP_EXIT':'重复出库申报'}
GATE_STATUS = {'confirmed':'已确认出闸','wrongful':'误出闸','unreported':'未申报出闸',
               'duplicate':'重复回执','unknown':'无法结清','pending':'待处理'}
EXIT_KINDS = {'declared_not_confirmed':'仅申报未出闸','wrongful_exit':'误出闸','unreported_exit':'未申报出闸'}
