package io.lexiflow.buildlogic

/** 根项目与模块共享同一次解析的完整身份；只读值不能被后续模块覆盖。 */
data class BuildIdentityExtension(val json: String, val softwareVersion: String)
