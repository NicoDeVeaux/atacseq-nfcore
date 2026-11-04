nextflow.enable.dsl = 2

params.modules_config = null
params.output_json = 'library-labels.json'

process PICARD_ADDORREPLACEREADGROUPS {
    input:
    val meta

    output:
    path '*.readgroups.json'

    script:
    def receipt = groovy.json.JsonOutput.toJson([
        kind: 'read_groups', process: task.process.toString(), meta: meta,
        args: task.ext.args.toString(), prefix: task.ext.prefix.toString()
    ])
    """
    cat > '${meta.id}.readgroups.json' <<'POC_JSON'
    ${receipt}
    POC_JSON
    """
}

process PICARD_MARKDUPLICATES {
    input:
    val meta

    output:
    path 'markduplicates.json'

    script:
    def receipt = groovy.json.JsonOutput.toJson([
        kind: 'mark_duplicates', process: task.process.toString(), meta: meta,
        args: task.ext.args.toString(), prefix: task.ext.prefix.toString()
    ])
    """
    cat > markduplicates.json <<'POC_JSON'
    ${receipt}
    POC_JSON
    """
}

workflow FASTQ_ALIGN_CHROMAP {
    take:
    libraries
    main:
    PICARD_ADDORREPLACEREADGROUPS(libraries)
    emit:
    labels = PICARD_ADDORREPLACEREADGROUPS.out
}

workflow MERGED_LIBRARY_MARKDUPLICATES_PICARD {
    main:
    PICARD_MARKDUPLICATES(channel.value([id: 'LIB_A_REP1', single_end: false]))
    emit:
    labels = PICARD_MARKDUPLICATES.out
}

workflow ATACSEQ {
    take:
    libraries
    main:
    FASTQ_ALIGN_CHROMAP(libraries)
    MERGED_LIBRARY_MARKDUPLICATES_PICARD()
    emit:
    labels = FASTQ_ALIGN_CHROMAP.out.labels.mix(MERGED_LIBRARY_MARKDUPLICATES_PICARD.out.labels)
}

workflow NFCORE_ATACSEQ {
    take:
    libraries
    main:
    ATACSEQ(libraries)
    emit:
    labels = ATACSEQ.out.labels
}

workflow {
    def libraries = channel.of(
        [id: 'LIB_A_REP1_T1', single_end: false],
        [id: 'LIB_A_REP1_T2', single_end: false],
        [id: 'LIB_B_REP1_T1', single_end: false]
    )
    NFCORE_ATACSEQ(libraries)
    NFCORE_ATACSEQ.out.labels.collect().subscribe { paths ->
        def source = file(params.modules_config)
        def sha256 = { bytes -> java.security.MessageDigest.getInstance('SHA-256').digest(bytes).encodeHex().toString() }
        def rows = paths.collect { new groovy.json.JsonSlurper().parseText(it.text) }
        assert rows.count { it.kind == 'read_groups' } == 3
        assert rows.count { it.kind == 'mark_duplicates' } == 1
        file(params.output_json).text = groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson([
            schema: 'atacseq-production-library-selector-probe/v1',
            nextflow_version: nextflow.version.toString(),
            modules_config_sha256: sha256.call(source.bytes),
            rows: rows.sort { a, b -> a.process <=> b.process ?: a.meta.id <=> b.meta.id }
        ])) + '\n'
    }
}
