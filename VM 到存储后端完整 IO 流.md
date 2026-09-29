# VM 到存储后端完整 IO 流

当前已完成 NFS 的四种 IO 模型。vhost 和 iSCSI 按相同模型预留，后续采集直接补入对应章节。

## NFS

### 高深度 4K 读

采样目录：`/mnt/xty/test/perf-captures/nfs-vm-to-asan-highread-20260929/`

虚拟机：`10.174.99.19`；宿主机和存储节点：`10.174.188.68`；卷：`fd3fc381_vs_vol_rep3`。

模型：`libaio`、`direct=1`、`4K randread`、`iodepth=64`、`numjobs=8`，总在途深度 512。

```
请求下发

主机：10.174.99.19（guest）
进程名：fio  PID 89634
线程名：fio  TID 89640、89641、89642、89643、89644、89645、89646、89647
    io_submit

主机：10.174.99.19（guest）
进程名：fio  PID 89634（guest kernel 上下文）
线程名：fio  TID 89640、89641、89642、89643、89644、89645、89646、89647
    SyS_io_submit
    do_io_submit
    blkdev_aio_read
    generic_file_aio_read
    blkdev_direct_IO
    do_blockdev_direct_IO
    submit_bio
    generic_make_request
    blk_mq_make_request
    blk_finish_plug
    blk_flush_plug_list
    blk_mq_flush_plug_list
    blk_mq_sched_insert_requests
    blk_mq_run_hw_queue
    __blk_mq_run_hw_queue
    blk_mq_sched_dispatch_requests
    blk_mq_dispatch_rq_list
    virtio_queue_rq
    __virtblk_add_req
    virtqueue_add_sgs
    virtqueue_notify
    vp_notify

切线程：[VM exit，切到 188.68 对应的 QEMU vCPU 线程]

主机：188.68
进程名：kvm  PID 2637246
线程名：CPU 0/KVM TID 2637920、CPU 1/KVM TID 2637923、CPU 2/KVM TID 2637925、CPU 3/KVM TID 2637927、CPU 4/KVM TID 2637929、CPU 5/KVM TID 2637931、CPU 6/KVM TID 2637932、CPU 7/KVM TID 2637933
    vmx_handle_exit
    handle_ept_misconfig
    kvm_io_bus_write
    __kvm_io_bus_write
    ioeventfd_write
    eventfd_signal_mask

切线程：[ioeventfd 唤醒 QEMU 主线程 TID 2637246]

主机：188.68
进程名：kvm  PID 2637246
线程名：主线程  TID 2637246
    aio_dispatch
    aio_dispatch_handler
    virtio_queue_notify_aio_vq.part.28
    virtio_blk_handle_vq
    virtqueue_split_pop
    virtio_blk_handle_request
    virtio_blk_submit_multireq
    blk_aio_preadv
    blk_aio_prwv
    aio_co_enter
    qemu_aio_coroutine_enter
    qemu_coroutine_switch
    coroutine_trampoline
    blk_aio_read_entry
    blk_do_preadv
    bdrv_co_preadv_part
    bdrv_aligned_preadv
    bdrv_driver_preadv
    qcow2_co_preadv_part
    qcow2_add_task
    qcow2_co_preadv_task_entry
    bdrv_co_preadv_part
    bdrv_aligned_preadv
    bdrv_driver_preadv
    nfs_co_preadv
    nfs_preadv_async
    nfs_task_enqueue
    eventfd_write

切线程：[libnfs eventfd 唤醒 QEMU NFS poller TID 2637705]

主机：188.68
进程名：kvm  PID 2637246
线程名：NFS poller  TID 2637705
    nfs_poller_thread
    nfs_event_handler
    nfs_async_task
    rpc_nfs3_read_async
    rpc_allocate_pdu
    rpc_allocate_pdu2
    rpc_queue_pdu
    rpc_event_fd_service
    sendmsg
    unix_stream_sendmsg

切线程：[同机 /var/run/vs_nfs.socket 切到 asan-stord]

主机：188.68
进程名：asan-stord  PID 2562723
线程名：glfs-nfs-2 TID 2563089、glfs-nfs-3 TID 2563090
    event_dispatch_epoll
    dispatch_handler
    socket_event_handler
    socket_event_poll_in
    rpc_transport_notify
    rpcsvc_notify
    rpcsvc_handle_rpc_call
    nfs3svc_read
    nfs3_read
    nfs3_fh_resolve_resume
    nfs3_fh_resolve_inode
    nfs3_fh_resolve_inode_done
    nfs3_read_resume
    nfs_fd_resume_or_enqueue
    nfs3_read_fd_resume
    nfs_fop_read
    io_stats_readv
    pipeline_readv

切线程：[pipeline-opt 切到 gfapi-opt-2 TID 2563101]

主机：188.68
进程名：asan-stord  PID 2562723
线程名：gfapi-opt-2  TID 2563101
    pipeline_readv_resume
    ior_readv
    snapshot_readv
    shard_readv
    shard_readv_fop_get_prebuf
    shard_readv_do
    pipeline_readv

切线程：[pipeline-core 切到 gfapi-core-0..5]

主机：188.68
进程名：asan-stord  PID 2562723
线程名：gfapi-core-0 TID 2563072、gfapi-core-1 TID 2563073、gfapi-core-2 TID 2563074、gfapi-core-3 TID 2563075、gfapi-core-4 TID 2563076、gfapi-core-5 TID 2563077
    pipeline_readv_resume
    route_readv
    afr_readv
    afr_readv_resume
    afr_readv_nor
    afr_readv_cont
    vclnt_readv
    client_readv
    client3_3_readv
    rpc_clnt_submit_direct
    vsshm_submit_direct

切线程：[VSSHM 切进程，切到对应 DATA glusterfsd 的 glfsd-net-0]

主机：188.68
进程名：glusterfsd  PID 2582635、2582984、2583207、2583985、2584277、2585470
线程名：glfsd-net-0 TID 2582651、2583045、2583239、2584002、2584311、2585561
    connection_eventfd_handler
    on_key_received
    rpcsvc_handle_rpc_call
    server3_3_readv
    server_readv_direct_resume
    io_stats_readv
    rt_auth_readv
    cio_readv
    cio_push
    vcotask_wrap
    cio_rw_resume
    cio_readv_resume
    exclusive_readv
    vss_readv
    pl_readv
    prealloc_readv
    wcc_readv
    st_readv
    tier_read
    send_req_direct

切协程：[cio_push -> vcotask_wrap -> cio_rw_resume，仍在同一个 glfsd-net-0 TID]

切线程：[tier_comm.sock 切到 asan-stord 的 tierd-core]

主机：188.68
进程名：asan-stord  PID 2562723
线程名：tierd-core  TID 2562784、2562787
    __on_channel_read
    __channel_recv
    comm_deal_pkg
    tfs_comm_recv
    coroutine_enter
    tfs_req_handler_co_wrapper
    tfs_on_read
    tfs_pread
    tfs_inode_generic_read
    tfs_cdev_co_rw_batch
    tfs_cdev_iouring_io_submit
    iouring_dispatch
    vqueue_push

切线程：[io_uring 提交队列切到 tierd-aio]

主机：188.68
进程名：asan-stord  PID 2562723
线程名：tierd-aio  TID 2562796、2562798
    iouring_aio_submit_handler
    iouring_submit_pending
    io_uring_enter

主机：188.68
设备：dm-15 -> nvme1n1p1、dm-20 -> nvme0n1p1


完成返回

切线程：[io_uring 完成切回 tierd-core]

主机：188.68
进程名：asan-stord  PID 2562723
线程名：tierd-core  TID 2562784、2562787
    iouring_aio_cqe_handler
    coroutine_enter
    tfs_pread
    tfs_req_handler_co_wrapper
    comm_send_rsp
    channel_send_with_notify

切线程：[tier_comm.sock 切到对应 DATA glusterfsd 的 libcomm]

主机：188.68
进程名：glusterfsd  PID 2582635、2582984、2583207、2583985、2584277、2585470
线程名：libcomm TID 2590124、2589937、2590252、2590582、2590780、2590826
    __on_channel_read
    __channel_recv
    comm_deal_pkg
    tier_read_cbk
    wcc_readv_cbk
    vss_readv_cbk
    cio_readv_cbk
    rt_auth_readv_cbk
    io_stats_readv_cbk
    server_readv_direct_cbk
    vsshm_submit_direct

切线程：[VSSHM 完成切到 asan-stord 的 gfapi-net-0..5]

主机：188.68
进程名：asan-stord  PID 2562723
线程名：gfapi-net-0 TID 2563080、gfapi-net-1 TID 2563081、gfapi-net-2 TID 2563082、gfapi-net-3 TID 2563084、gfapi-net-4 TID 2563085、gfapi-net-5 TID 2563086
    connection_eventfd_handler
    client3_3_readv_direct_cbk
    vclnt_readv_cbk
    afr_readv_cbk
    route_readv_cbk
    pipeline_readv_cbk
    shard_readv_do_cbk_main
    ior_readv_cbk
    pipeline_readv_cbk
    io_stats_readv_cbk
    nfs3svc_read_cbk
    nfs3_read_reply
    socket_submit

切线程：[同一条 /var/run/vs_nfs.socket 切到 QEMU NFS poller TID 2637705]

主机：188.68
进程名：kvm  PID 2637246
线程名：NFS poller  TID 2637705
    nfs_poller_thread
    rpc_service
    rpc_event_fd_service
    rpc_process_pdu
    nfs_iovs_from_buf
    nfs_co_generic_rw_cb
    aio_bh_enqueue
    aio_notify
    event_notifier_set

切线程：[eventfd 唤醒 QEMU 主线程 TID 2637246]

主机：188.68
进程名：kvm  PID 2637246
线程名：主线程  TID 2637246
    main_loop_wait
    g_main_context_dispatch
    g_main_dispatch
    aio_ctx_dispatch
    aio_dispatch
    aio_bh_poll
    nfs_co_generic_bh_cb
    qemu_coroutine_switch
    nfs_co_preadv
    bdrv_driver_preadv
    bdrv_aligned_preadv
    bdrv_co_preadv_part
    qcow2_co_preadv_task_entry
    qcow2_co_preadv_part
    blk_aio_complete.part.8
    virtio_blk_rw_complete
    virtio_blk_req_complete
    virtqueue_push
    virtio_notify_irqfd
    event_notifier_set

主机：188.68
进程名：kvm  PID 2637246（host kernel 上下文）
线程名：主线程  TID 2637246
    eventfd_write
    irqfd_wakeup
    kvm_arch_set_irq_inatomic
    kvm_irq_delivery_to_apic_fast
    __apic_accept_irq

切线程：[IRQfd 向 guest 注入 virtio 中断]

主机：10.174.99.19（guest）
进程名：guest kernel
线程名：hardirq
    vring_interrupt
    virtblk_done
    virtqueue_get_buf
    blk_mq_complete_request
    __blk_mq_complete_request

切线程：[guest hardirq 切到 softirq]

主机：10.174.99.19（guest）
进程名：guest kernel
线程名：softirq
    blk_done_softirq
    virtblk_request_done
    blk_mq_end_request
    blk_update_request
    bio_endio
    dio_complete
    aio_complete

切线程：[aio_complete 唤醒 fio 工作线程]

主机：10.174.99.19（guest）
进程名：fio  PID 89634
线程名：fio  TID 89640、89641、89642、89643、89644、89645、89646、89647
    io_getevents
    SyS_io_getevents
    aio_read_events

IO 完成
```

QEMU 到 `asan-stord` 使用同机 Unix socket，不经过物理网卡。QEMU 没有独立 iothread：主线程 `2637246` 负责 virtio-blk、QCOW2、NFS 协程和 virtio 完成，NFS poller `2637705` 负责 NFS RPC 收发。

原始采样：QEMU 60 秒 LBR 42,343 samples、`asan-stord` 60 秒 LBR 43,177 samples，均为 0 lost；guest 使用无覆盖丢失的 ftrace。

### 高深度 4K 写

采样目录：`/mnt/xty/test/perf-captures/nfs-vm-full-high-write-20260929/`

模型：`libaio`、`direct=1`、`4K randwrite`、`iodepth=64`、`numjobs=8`，总在途深度 512。

一个逻辑写生成两份 4K 数据：本地副本写 `188.68`，远端副本写 `188.66`。仲裁成员只参与锁、changelog 和 xattr，不写 4K 数据。

```
请求下发

主机：10.174.99.19（guest）
进程名：fio  PID 126515
线程名：fio  TID 126517、126518、126519、126520、126521、126522、126523、126524
    io_submit

主机：10.174.99.19（guest）
进程名：fio  PID 126515（guest kernel 上下文）
线程名：fio  TID 126517、126518、126519、126520、126521、126522、126523、126524
    SyS_io_submit
    do_io_submit
    blkdev_aio_write
    __generic_file_aio_write
    generic_file_direct_write
    blkdev_direct_IO
    do_blockdev_direct_IO
    submit_bio
    generic_make_request
    blk_mq_make_request
    blk_finish_plug
    blk_flush_plug_list
    blk_mq_flush_plug_list
    blk_mq_sched_insert_requests
    blk_mq_run_hw_queue
    __blk_mq_run_hw_queue
    blk_mq_sched_dispatch_requests
    blk_mq_dispatch_rq_list
    virtio_queue_rq
    __virtblk_add_req
    virtqueue_add_sgs
    virtqueue_notify
    vp_notify

切线程：[VM exit，落到 188.68 对应的 QEMU vCPU 线程]


主机：188.68（QEMU 宿主，SSH 22346）
进程名：kvm  PID 2637246
线程名：CPU 0/KVM TID 2637920、CPU 1/KVM TID 2637923、CPU 2/KVM TID 2637925、CPU 3/KVM TID 2637927、CPU 4/KVM TID 2637929、CPU 5/KVM TID 2637931、CPU 6/KVM TID 2637932、CPU 7/KVM TID 2637933
    vmx_handle_exit
    handle_ept_misconfig
    kvm_io_bus_write
    __kvm_io_bus_write
    ioeventfd_write
    eventfd_signal_mask

切线程：[ioeventfd 唤醒 QEMU 主线程 TID 2637246]

主机：188.68（QEMU 宿主，SSH 22346）
进程名：kvm  PID 2637246
线程名：主线程  TID 2637246
    aio_dispatch
    aio_dispatch_handler
    virtio_queue_notify_aio_vq.part.28
    virtio_blk_handle_vq
    virtqueue_split_pop
    virtio_blk_handle_request
    virtio_blk_submit_multireq
    blk_aio_pwritev
    blk_aio_prwv
    aio_co_enter
    qemu_aio_coroutine_enter
    qemu_coroutine_switch
    coroutine_trampoline
    blk_aio_write_entry
    blk_do_pwritev_part
    bdrv_co_pwritev_part
    bdrv_aligned_pwritev
    bdrv_driver_pwritev
    qcow2_co_pwritev_part
    qcow2_add_task
    qcow2_co_pwritev_task_entry
    qcow2_co_pwritev_task
    bdrv_co_pwritev_part
    bdrv_aligned_pwritev
    bdrv_driver_pwritev
    nfs_co_pwritev
    nfs_pwritev_async
    nfs_task_enqueue
    eventfd_write

切线程：[libnfs eventfd 唤醒 QEMU NFS poller TID 2637705]

主机：188.68（QEMU 宿主，SSH 22346）
进程名：kvm  PID 2637246
线程名：NFS poller  TID 2637705
    nfs_poller_thread
    nfs_event_handler
    nfs_async_task
    rpc_nfs3_writev_async
    rpc_allocate_pdu
    rpc_queue_pdu
    rpc_event_fd_service
    sendmsg
    unix_stream_sendmsg

切线程：[同机 /var/run/vs_nfs.socket 切到 asan-stord]

主机：188.68
进程名：asan-stord  PID 2562723
线程名：glfs-nfs-2 TID 2563089、glfs-nfs-3 TID 2563090
    event_dispatch_epoll
    dispatch_handler
    socket_event_handler
    socket_event_poll_in
    rpc_transport_notify
    rpcsvc_notify
    rpcsvc_handle_rpc_call
    nfs3svc_write
    nfs3_write
    nfs3_write_resume
    nfs_fd_resume_or_enqueue
    nfs3_write_continue
    nfs3_write_fd_resume
    nfs_fop_write
    io_stats_writev
    pipeline_writev

切线程：[pipeline-opt 切到 gfapi-opt-2 TID 2563101]

主机：188.68
进程名：asan-stord  PID 2562723
线程名：gfapi-opt-2  TID 2563101
    pipeline_writev_resume
    ior_writev
    snapshot_writev
    trash_writev
    shard_writev
    shard_fop_get_prebuf
    shard_writev_prepare_subio
    shard_writev_do
    pipeline_writev

切线程：[pipeline-core 切到 gfapi-core-0..5]

主机：188.68
进程名：asan-stord  PID 2562723
线程名：gfapi-core-0 TID 2563072、gfapi-core-1 TID 2563073、gfapi-core-2 TID 2563074、gfapi-core-3 TID 2563075、gfapi-core-4 TID 2563076、gfapi-core-5 TID 2563077
    pipeline_writev_resume
    route_writev
    afr_writev
    afr_writev_cont
    afr_do_writev
    afr_transaction
    afr_lock_rec
    afr_nonblocking_inodelk
    afr_nonblocking_inodelk_cbk
    afr_internal_lock_finish
    afr_changelog_pre_op
    afr_changelog_pre_op_cbk
    afr_txn_arbitrate_fop
    afr_writev_wind
    __afr_writev_wind


数据分支 1：本地 188.68

主机：188.68
进程名：asan-stord  PID 2562723
线程名：gfapi-core-0..5  TID 2563072、2563073、2563074、2563075、2563076、2563077
    __afr_writev_single_wind
    vclnt_writev
    client_writev
    client3_3_writev
    client_submit_write_request
    rpc_clnt_submit_fast
    vsshm_submit_request

切线程：[VSSHM 切进程，切到本地 DATA glusterfsd 的 glfsd-net-0]

主机：188.68
进程名：glusterfsd  PID 2582635、2582984、2583207、2583985、2584277、2585470
线程名：glfsd-net-0 TID 2582651、2583045、2583239、2584002、2584311、2585561
    connection_eventfd_handler
    on_key_received
    rpcsvc_handle_rpc_call
    server3_3_writev
    io_stats_writev
    rt_auth_writev
    cio_writev
    cio_push
    vcotask_wrap
    cio_rw_resume
    cio_writev_resume
    vss_writev
    pl_writev
    drc_writev
    dr_writev
    prealloc_writev
    wcc_writev
    afc_writev
    st_writev
    tier_resume
    tier_write
    send_req_direct

切协程：[CIO 在当前 glfsd-net-0 TID 内恢复执行，不切 OS 线程]

切线程：[tier_comm.sock 切到本机 asan-stord 的 tierd-core]

主机：188.68
进程名：asan-stord  PID 2562723
线程名：tierd-core  TID 2562784、2562787
    __on_channel_read
    __channel_recv
    comm_deal_pkg
    tfs_comm_recv
    coroutine_enter
    tfs_req_handler_co_wrapper
    tfs_on_write
    tfs_pwrite
    tfs_inode_generic_write
    tfs_cdev_co_rw_batch
    tfs_cdev_iouring_io_submit
    iouring_dispatch
    vqueue_push

切线程：[io_uring 提交队列切到 tierd-aio]

主机：188.68
进程名：asan-stord  PID 2562723
线程名：tierd-aio  TID 2562796、2562798
    iouring_aio_submit_handler
    iouring_submit_pending
    io_uring_enter

主机：188.68
设备：dm-15 -> nvme1n1p1、dm-20 -> nvme0n1p1


数据分支 2：远端 188.66

主机：188.68
进程名：asan-stord  PID 2562723
线程名：gfapi-core-0..5  TID 2563072、2563073、2563074、2563075、2563076、2563077
    __afr_writev_single_wind
    vclnt_writev
    client_writev
    client3_3_writev
    client_submit_write_request
    rpc_clnt_submit_fast
    socket_submit

切线程：[TCP 跨机切到 188.66 DATA glusterfsd 的 glfsd-net-0]

主机：188.66
进程名：glusterfsd  PID 2408276、2408521、2408839、2409334、2410016、2410898
线程名：glfsd-net-0 TID 2408327、2408546、2408887、2409395、2410076、2410973
    socket_event_poll_in
    rpcsvc_handle_rpc_call
    server3_3_writev
    io_stats_writev
    rt_auth_writev
    cio_writev
    cio_push
    vcotask_wrap
    cio_rw_resume
    cio_writev_resume
    vss_writev
    pl_writev
    drc_writev
    dr_writev
    prealloc_writev
    wcc_writev
    afc_writev
    st_writev
    tier_resume
    tier_write
    send_req_direct

切协程：[CIO 在当前 glfsd-net-0 TID 内恢复执行，不切 OS 线程]

切线程：[tier_comm.sock 切到 188.66 asan-stord 的 tierd-core]

主机：188.66
进程名：asan-stord  PID 2389614
线程名：tierd-core  TID 2389756、2389759
    __on_channel_read
    __channel_recv
    comm_deal_pkg
    tfs_comm_recv
    coroutine_enter
    tfs_req_handler_co_wrapper
    tfs_on_write
    tfs_pwrite
    tfs_inode_generic_write
    tfs_cdev_co_rw_batch
    tfs_cdev_iouring_io_submit
    iouring_dispatch
    vqueue_push

切线程：[io_uring 提交队列切到 tierd-aio]

主机：188.66
进程名：asan-stord  PID 2389614
线程名：tierd-aio  TID 2389768、2389770
    iouring_aio_submit_handler
    iouring_submit_pending
    io_uring_enter

主机：188.66
设备：dm-17 -> nvme1n1p1、dm-20 -> nvme0n1p1


控制分支 3：仲裁，不写 4K 数据

主机：188.68
进程名：glusterfsd（DATA_ARBITER）  PID 2582985、2583641
线程名：glfsd-net-0  TID 2583081、2583684
    server3_3_inodelk
    server3_3_finodelk
    rt_auth_inodelk
    rt_auth_finodelk
    pipeline_inodelk
    pipeline_finodelk
    pl_inodelk
    pl_finodelk
    server3_3_fxattrop
    pipeline_fxattrop
    pl_fxattrop
    arbiter_fxattrop
    entry_fxattrop
    posix_fxattrop

主机：188.66
进程名：glusterfsd（DATA_ARBITER）  PID 2408838、2409333
线程名：glfsd-net-0  TID 2408896、2409403
    server3_3_inodelk
    server3_3_finodelk
    rt_auth_inodelk
    rt_auth_finodelk
    pipeline_inodelk
    pipeline_finodelk
    pl_inodelk
    pl_finodelk
    server3_3_fxattrop
    pipeline_fxattrop
    pl_fxattrop
    arbiter_fxattrop
    entry_fxattrop
    posix_fxattrop


完成返回：本地数据副本

切线程：[本地 NVMe 完成切回 tierd-core]

主机：188.68
进程名：asan-stord  PID 2562723
线程名：tierd-core  TID 2562784、2562787
    iouring_aio_cqe_handler
    iouring_reap_ring
    coroutine_enter
    tfs_pwrite
    tfs_req_handler_co_wrapper
    comm_send_rsp
    channel_send_with_notify

切线程：[tier_comm.sock 切到本地 DATA glusterfsd 的 libcomm]

主机：188.68
进程名：glusterfsd  PID 2582635、2582984、2583207、2583985、2584277、2585470
线程名：libcomm TID 2590124、2589937、2590252、2590582、2590780、2590826
    __on_channel_read
    __channel_recv
    comm_deal_pkg
    tier_write_cbk
    afc_writev_bypass_cbk
    wcc_writev_cbk
    dr_writev_cbk
    drc_common_write_cbk
    vss_writev_cbk
    cio_writev_cbk
    rt_auth_writev_cbk
    io_stats_writev_cbk
    server_writev_fast_cbk
    server_submit_write_reply
    vsshm_submit_reply


完成返回：远端数据副本

切线程：[远端 NVMe 完成切回 tierd-core]

主机：188.66
进程名：asan-stord  PID 2389614
线程名：tierd-core  TID 2389756、2389759
    iouring_aio_cqe_handler
    iouring_reap_ring
    coroutine_enter
    tfs_pwrite
    tfs_req_handler_co_wrapper
    comm_send_rsp
    channel_send_with_notify

切线程：[tier_comm.sock 切到远端 DATA glusterfsd 的 libcomm]

主机：188.66
进程名：glusterfsd  PID 2408276、2408521、2408839、2409334、2410016、2410898
线程名：libcomm TID 2415259、2415079、2415521、2415641、2416091、2416301
    __on_channel_read
    __channel_recv
    comm_deal_pkg
    tier_write_cbk
    afc_writev_bypass_cbk
    wcc_writev_cbk
    dr_writev_cbk
    drc_common_write_cbk
    vss_writev_cbk
    cio_writev_cbk
    rt_auth_writev_cbk
    io_stats_writev_cbk
    server_writev_fast_cbk
    server_submit_write_reply
    socket_submit


双副本汇合并返回 NFS

切线程：[本地 VSSHM 完成和远端 TCP 完成分别切到 asan-stord 的 gfapi-net-0..5]

主机：188.68
进程名：asan-stord  PID 2562723
线程名：gfapi-net-0 TID 2563080、gfapi-net-1 TID 2563081、gfapi-net-2 TID 2563082、gfapi-net-3 TID 2563084、gfapi-net-4 TID 2563085、gfapi-net-5 TID 2563086
    connection_eventfd_handler
    socket_event_poll_in
    client3_3_writev_cbk
    vclnt_writev_cbk
    afr_writev_wind_cbk
    afr_write_finish
    afr_transaction_resume
    afr_changelog_post_op
    afr_changelog_post_op_cbk
    afr_changelog_post_op_done
    afr_unlock
    afr_unlock_inodelk
    afr_unlock_inodelk_cbk
    afr_writev_done
    __afr_writev_done
    route_writev_cbk
    pipeline_writev_cbk
    shard_writev_do_cbk_main
    shard_update_file_size
    shard_writev_post_update_file_size
    trash_writev_cbk
    snapshot_bypass_writev_cbk
    ior_writev_cbk
    pipeline_writev_cbk
    io_stats_writev_cbk
    nfs_fop_truncate_cbk
    nfs3svc_write_cbk
    nfs3_write_reply
    nfs3svc_submit_reply
    socket_submit

说明：[afr_writev_wind_cbk 聚合两份数据副本结果；仲裁完成控制操作后，AFR 执行 post-op 和 unlock]

切线程：[同一条 /var/run/vs_nfs.socket 切到 QEMU NFS poller TID 2637705]

主机：188.68（QEMU 宿主，SSH 22346）
进程名：kvm  PID 2637246
线程名：NFS poller  TID 2637705
    nfs_poller_thread
    rpc_service
    rpc_event_fd_service
    rpc_process_pdu
    nfs_co_generic_rw_cb
    aio_bh_enqueue
    aio_notify
    event_notifier_set

切线程：[eventfd 唤醒 QEMU 主线程 TID 2637246]

主机：188.68（QEMU 宿主，SSH 22346）
进程名：kvm  PID 2637246
线程名：主线程  TID 2637246
    aio_bh_poll
    nfs_co_generic_bh_cb
    qemu_coroutine_switch
    nfs_co_pwritev
    bdrv_driver_pwritev
    bdrv_aligned_pwritev
    bdrv_co_pwritev_part
    qcow2_co_pwritev_task
    qcow2_co_pwritev_task_entry
    qcow2_co_pwritev_part
    blk_aio_complete.part.8
    virtio_blk_rw_complete
    virtio_blk_req_complete
    virtqueue_push
    virtio_notify_irqfd
    event_notifier_set

主机：188.68（host kernel 上下文）
进程名：kvm  PID 2637246
线程名：主线程  TID 2637246
    eventfd_write
    irqfd_wakeup
    kvm_arch_set_irq_inatomic
    kvm_irq_delivery_to_apic_fast
    __apic_accept_irq

切线程：[IRQfd 唤醒对应 QEMU vCPU，并向 guest 注入 virtio 中断]

主机：10.174.99.19（guest）
进程名：guest kernel
线程名：hardirq
    vring_interrupt
    virtblk_done
    virtqueue_get_buf
    blk_mq_complete_request
    __blk_mq_complete_request

切线程：[guest hardirq 切到 softirq]

主机：10.174.99.19（guest）
进程名：guest kernel
线程名：softirq
    blk_done_softirq
    virtblk_request_done
    blk_mq_end_request
    blk_update_request
    bio_endio
    dio_complete
    aio_complete

切线程：[aio_complete 唤醒提交该 IO 的 fio 线程]

主机：10.174.99.19（guest）
进程名：fio  PID 126515
线程名：fio  TID 126517、126518、126519、126520、126521、126522、126523、126524
    io_getevents
```

采样文件：

- `qemu-lbr.data`：60 秒，39,062 samples，0 lost。
- `storage-188.68-lbr.data`：60 秒，81,161 samples，0 lost。
- `storage-188.66-lbr.data`：60 秒，30,917 samples，0 lost。
- `vm-high-write-guest-ftrace.txt`：47,163 entries，buffer 无覆盖。
- `vm-high-write-guest-stack-ftrace.txt`：guest 内核写路径带栈证据。