//! Standalone Windows encrypted bill receiver. No ledger writes.
fn main() {
    #[cfg(any(windows, target_os = "macos"))]
    {
        let args: Vec<String> = std::env::args().skip(1).collect();
        let result = (|| -> Result<serde_json::Value, String> {
            let directory = app_lib::ledger_cli_support::data_directory()?;
            std::fs::create_dir_all(&directory).map_err(|_| "本机目录不可用。")?;
            let lock = std::fs::OpenOptions::new()
                .create(true)
                .truncate(false)
                .read(true)
                .write(true)
                .open(directory.join("bill-inbox-cli.lock"))
                .map_err(|_| "无法锁定收件箱。")?;
            lock.try_lock().map_err(|_| "另一个收件箱操作正在进行。")?;
            let vault = app_lib::ledger_cli_support::cli_vault()?;
            match args.first().map(String::as_str) {
                Some("receive") if args.len() == 2 || args.len() == 3 => {
                    let output = std::path::Path::new(&args[1]);
                    let page = app_lib::bill_inbox::receive_page(
                        &vault,
                        output,
                        args.get(2).map(String::as_str),
                    )?;
                    Ok(serde_json::json!(page))
                }
                Some("acknowledge") if args.len() == 3 => {
                    app_lib::bill_inbox::acknowledge(&vault, &args[1], &args[2])?;
                    Ok(serde_json::json!({"ok":true}))
                }
                _ => Err("参数无效。".into()),
            }
        })();
        match result {
            Ok(value) => println!("{value}"),
            Err(_) => {
                eprintln!("账单收件箱操作失败；请检查参数、配对、凭据和网络后重试。");
                std::process::exit(2);
            }
        }
    }
    #[cfg(not(any(windows, target_os = "macos")))]
    {
        eprintln!("Bill inbox CLI requires Windows or macOS.");
        std::process::exit(2);
    }
}
