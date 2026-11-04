nextflow.enable.dsl = 2

include { TRIMGALORE } from "${params.repo}/modules/nf-core/trimgalore/main.nf"

workflow {
    TRIMGALORE(channel.of([
        [id: params.case_name, single_end: false],
        [file(params.read1, checkIfExists: true), file(params.read2, checkIfExists: true)]
    ]))
    TRIMGALORE.out.reads.subscribe { row ->
        def (meta, reads) = row
        def receipt = [meta: meta, reads: [reads].flatten().collect { read -> read.toString() }]
        file(params.receipt).text = groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(receipt)) + '\n'
    }
}
