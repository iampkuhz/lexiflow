/** Do not let an unresponsive extension message keep a control busy indefinitely. */
export function bounded<T>(operation: Promise<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("message timeout")), 5000);
    operation.then(resolve, reject).finally(() => clearTimeout(timer));
  });
}
