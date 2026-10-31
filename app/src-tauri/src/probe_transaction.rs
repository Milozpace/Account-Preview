use std::{
    fs::{self, File, OpenOptions},
    path::Path,
};

const LOCK_FILE: &str = "synthetic-probe.lock";

fn acquire(directory: &Path) -> Result<File, String> {
    fs::create_dir_all(directory).map_err(|_| "无法建立验证事务目录。")?;
    let guard = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(directory.join(LOCK_FILE))
        .map_err(|_| "无法打开验证事务锁，未修改密钥或缓存。")?;
    guard
        .lock()
        .map_err(|_| "无法锁定验证事务，未修改密钥或缓存。")?;
    Ok(guard)
}

pub fn with_transaction<T>(
    directory: &Path,
    operation: impl FnOnce() -> Result<T, String>,
) -> Result<T, String> {
    // Retain the independently opened OS lock for the entire vault + cache
    // transaction. Closing this handle releases it, including on errors.
    // Keep the sidecar: unlinking it would let another process lock a new file.
    let _guard = acquire(directory)?;
    operation()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        io::{BufRead, BufReader, Write},
        process::{Child, Command, Stdio},
        sync::mpsc,
        thread,
        time::Duration,
    };

    #[test]
    fn independently_opened_handles_cannot_enter_until_transaction_guard_drops() {
        let directory = tempfile::tempdir().unwrap();
        let first = acquire(directory.path()).unwrap();
        let independently_opened = OpenOptions::new()
            .read(true)
            .write(true)
            .open(directory.path().join(LOCK_FILE))
            .unwrap();
        assert!(
            independently_opened.try_lock().is_err(),
            "a second handle entered the vault/cache transaction"
        );
        drop(first);
        independently_opened.try_lock().unwrap();
    }

    #[test]
    fn operation_failure_releases_transaction_for_next_instance() {
        let directory = tempfile::tempdir().unwrap();
        let result = with_transaction(directory.path(), || -> Result<(), String> {
            Err("synthetic failure".into())
        });
        assert!(result.is_err());
        let next = OpenOptions::new()
            .read(true)
            .write(true)
            .open(directory.path().join(LOCK_FILE))
            .unwrap();
        next.try_lock().unwrap();
    }

    fn spawn_counter_process(directory: &Path) -> (Child, mpsc::Receiver<()>) {
        let mut child = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "probe_transaction::tests::transaction_child_process",
                "--nocapture",
            ])
            .env("ACCOUNT_PROBE_TRANSACTION_TEST_DIRECTORY", directory)
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        let mut output = BufReader::new(child.stdout.take().unwrap());
        loop {
            let mut line = String::new();
            assert!(
                output.read_line(&mut line).unwrap() > 0,
                "child exited before readiness"
            );
            if line.contains("[probe-transaction-ready]") {
                break;
            }
        }
        let (entered, receiver) = mpsc::channel();
        thread::spawn(move || {
            for line in output.lines() {
                if line.unwrap().contains("[probe-transaction-entered]") {
                    let _ = entered.send(());
                }
            }
        });
        (child, receiver)
    }

    #[test]
    fn distinct_processes_serialize_whole_transactions_without_lost_updates() {
        let directory = tempfile::Builder::new()
            .prefix("account-probe-transactions-")
            .tempdir()
            .unwrap();
        fs::write(directory.path().join("counter.txt"), "100").unwrap();
        let held = acquire(directory.path()).unwrap();
        let (mut first, entered_first) = spawn_counter_process(directory.path());
        let (mut second, entered_second) = spawn_counter_process(directory.path());
        assert!(matches!(
            entered_first.recv_timeout(Duration::from_millis(50)),
            Err(mpsc::RecvTimeoutError::Timeout)
        ));
        assert!(matches!(
            entered_second.recv_timeout(Duration::from_millis(50)),
            Err(mpsc::RecvTimeoutError::Timeout)
        ));
        assert_eq!(
            fs::read_to_string(directory.path().join("counter.txt")).unwrap(),
            "100"
        );
        drop(held);
        entered_first.recv_timeout(Duration::from_secs(10)).unwrap();
        entered_second
            .recv_timeout(Duration::from_secs(10))
            .unwrap();
        assert!(first.wait().unwrap().success());
        assert!(second.wait().unwrap().success());
        assert_eq!(
            fs::read_to_string(directory.path().join("counter.txt")).unwrap(),
            "132"
        );
        assert_eq!(
            fs::read_to_string(directory.path().join("committed.txt")).unwrap(),
            "132"
        );
    }

    #[test]
    fn transaction_child_process() {
        let Some(directory) = std::env::var_os("ACCOUNT_PROBE_TRANSACTION_TEST_DIRECTORY") else {
            return;
        };
        let directory = fs::canonicalize(directory).unwrap();
        let temp_root = fs::canonicalize(std::env::temp_dir()).unwrap();
        assert_eq!(directory.parent(), Some(temp_root.as_path()));
        assert!(
            directory
                .file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("account-probe-transactions-")
        );
        println!("[probe-transaction-ready]");
        std::io::stdout().flush().unwrap();
        for iteration in 0..16 {
            with_transaction(&directory, || {
                if iteration == 0 {
                    println!("[probe-transaction-entered]");
                    std::io::stdout().flush().unwrap();
                }
                let counter = fs::read_to_string(directory.join("counter.txt"))
                    .unwrap()
                    .parse::<u32>()
                    .unwrap()
                    + 1;
                fs::write(directory.join("counter.txt"), counter.to_string()).unwrap();
                thread::sleep(Duration::from_millis(2));
                fs::write(directory.join("committed.txt"), counter.to_string()).unwrap();
                Ok(())
            })
            .unwrap();
        }
    }
}
