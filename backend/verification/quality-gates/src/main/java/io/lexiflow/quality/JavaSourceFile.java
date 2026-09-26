package io.lexiflow.quality;

import java.nio.file.Path;

/**
 * 一份已读取的 UTF-8 Java 源码。
 *
 * @param path 绝对路径。
 * @param relativePath 后端根目录相对路径。
 * @param text 源码文本。
 */
public record JavaSourceFile(Path path, String relativePath, String text) {}
